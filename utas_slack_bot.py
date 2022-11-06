"""
utas_slack_bot.py
This script sends update notifications to the UTAs slack on the progress
of grading from codePost.
"""

# ==============================================================================

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import codepost
import pytz
import yaml
from cryptography.fernet import Fernet, InvalidToken
from slack import WebClient
from slack.errors import SlackApiError

# ==============================================================================

# The cached data is separated into one folder for each course,
# then one file for each time the course's data is updated
CACHED_DATA_FOLDER = Path('./data')

ERROR_LOGS_FILE = CACHED_DATA_FOLDER / '_ERRORS.txt'

CONFIG_FILE = Path('config.yaml')

# yapf: disable
UPDATE_MESSAGE_TEMPLATE = (
    '*{assignment}*: {done:.2%} done ' +
    '({finalized} finalized, {drafts} drafts, ' +
    '{unclaimed} left to grade)'
)
GRADERS_RECENTLY_FINALIZED_TEMPLATE = (
    'Graders who recently finalized: {graders}'
)
# yapf: enable

# ==============================================================================

UTC_TZ = pytz.utc
EASTERN_TZ = pytz.timezone('US/Eastern')
DATE_FMT = '%Y-%m-%d'

NO_DAY = timedelta()
ONE_DAY = timedelta(days=1)

# ==============================================================================


def now_dt():
    """Returns the current datetime in UTC."""
    return UTC_TZ.localize(datetime.utcnow())


def now():
    """Returns the current time in UTC as a string.

    Since UTC does not observe daylight savings, this means that each call to
    this function should theoretically return something different.
    """
    return now_dt().strftime('%Y-%m-%d %H:%M:%S.%f')


def _error(msg, *args, **kwargs):
    """Returns the formatted message with the current timestamp."""
    formatted = msg.format(*args, **kwargs)
    print('Error:', formatted)
    return f'[{now()}] {formatted}'


# ==============================================================================


def _build_notification_msg(**kwargs):
    msg = UPDATE_MESSAGE_TEMPLATE.format(**kwargs)
    graders_finalized = kwargs.get('graders_finalized', set())
    if len(graders_finalized) > 0:
        graders = []
        for grader in graders_finalized:
            # remove "@princeton.edu"
            grader = grader[:-len('@princeton.edu')]
            # put in backticks
            graders.append(f'`{grader}`')
        graders_str = ', '.join(graders)
        msg += '\n' + GRADERS_RECENTLY_FINALIZED_TEMPLATE.format(
            graders=graders_str)
    return msg


# ==============================================================================


def send_slack_msg(slack_client, channel_id, msg, as_block=False):
    """Sends a message on Slack to the specified channel. If `as_block` is True,
    the message is sent as a markdown block.

    Returns the request response, and the error response or None if there was no
    error.
    """

    print('sending message to channel:', channel_id)
    print(msg)

    try:
        if as_block:
            response = slack_client.chat_postMessage(
                channel=channel_id,
                blocks=[{
                    'type': 'section',
                    'text': {
                        'type': 'mrkdwn',
                        'text': msg,
                    },
                }],
            )
        else:
            response = slack_client.chat_postMessage(
                channel=channel_id,
                text=msg,
            )
        error = None
    except SlackApiError as e:
        response = None
        error = e.response

    return response, error


# ==============================================================================


def check_assignment_updates(assignment, timestamp_key, cached=None):
    """Checks the codePost assignment for updates, comparing to the cached data.
    Returns whether there were updates and the new data to store for this
    assignment.
    """

    def max_int_num(nums, **kwargs):
        """Returns the maximum number of string numbers."""
        args = {}
        if 'default' in kwargs:
            args['default'] = kwargs['default']
        return max(nums, key=lambda a: (len(a), tuple(a)), **args)

    # maps: submission id -> timestamp ->
    #   status of "unclaimed", "draft", "finalized", or "deleted"
    if cached is not None:
        submissions = cached['submissions']
        runs = cached['runs']
        index = int(max_int_num(runs.keys(), default=0)) + 1
        deleted = set(submissions.keys())
    else:
        submissions = {}
        runs = {}
        index = 1
        deleted = set()
    index = str(index)

    updated_status = False

    def get_last_status(submission_id):
        """Returns the last status of the given submission."""
        NO_STATUS = {'status': 'unknown', 'grader': 'unknown'}

        submission_data = submissions.get(submission_id, {})

        if len(submission_data) == 0:
            return NO_STATUS

        max_key = max_int_num(submission_data.keys())
        return submission_data[max_key]

    def save_status(submission_id, status):
        """Writes the new status for the given submission."""
        nonlocal updated_status
        if submission_id not in submissions:
            submissions[submission_id] = {}
        submission_data = submissions[submission_id]
        submission_data[index] = status
        # when this is called, it is for sure a different status, so we can
        # conclude that the status has been updated
        updated_status = True

    num_total = 0
    num_finalized = 0
    num_drafts = 0
    num_unclaimed = 0

    # the graders who finalized between the cached and the current state
    graders_finalized = set()

    # get info about each submission
    for submission in assignment.list_submissions():
        submission_id = str(submission.id)
        deleted.discard(submission_id)

        last_status = get_last_status(submission_id)

        num_total += 1
        if submission.isFinalized:
            num_finalized += 1
            status = 'finalized'
            # check if it was finalized before
            if last_status['status'] != 'finalized':
                graders_finalized.add(submission.grader)
        elif submission.grader is not None:
            num_drafts += 1
            status = 'draft'
        else:
            num_unclaimed += 1
            status = 'unclaimed'

        current_status = {
            'status': status,
            'grader': submission.grader,
        }

        if last_status == current_status:
            # it's the same; don't update
            continue

        save_status(submission_id, current_status)

    # mark as deleted
    for submission_id in deleted:
        if submission_id not in submissions:
            # shouldn't happen, since these keys are taken from the dict
            continue

        last_status = get_last_status(submission_id)

        current_status = {
            'status': 'deleted',
            'grader': None,
        }

        if last_status == current_status:
            # it's the same; don't update
            continue

        save_status(submission_id, current_status)

    if updated_status:
        runs[index] = timestamp_key

    data = {
        'total': num_total,
        'finalized': num_finalized,
        'drafts': num_drafts,
        'unclaimed': num_unclaimed,
        'runs': runs,
        'submissions': submissions,
        'graders_finalized': graders_finalized,
    }

    if updated_status:
        changed = True
    elif num_total == 0 or num_finalized == 0:
        changed = False
    elif cached is None:
        # first time getting data
        changed = True
    else:
        changed = any(
            cached.get(key, None) != data[key]
            for key in ('total', 'finalized', 'drafts', 'unclaimed'))

    return changed, data


# ==============================================================================


def check_course_updates(slack_client,
                         channel,
                         course_period,
                         course,
                         assignments,
                         cached=None):
    """Checks the codePost course for updates, comparing to the cached data.
    Returns the new data to store for this course, and a list of errors.
    """
    if cached is None:
        cached = {}

    data = cached
    changed = False
    errors = []

    course_assignments = {a.name: a for a in course.assignments}

    for assignment in assignments:
        assignment_name = assignment['name']
        print('processing assignment:', assignment_name)
        if not assignment['valid_date_range']:
            print('not in the proper date range')
            continue
        if assignment_name not in course_assignments:
            errors.append(
                _error('Course "{}" does not have an assignment called "{}"',
                       course_period, assignment_name))
            continue
        assignment = course_assignments[assignment_name]
        assignment_cache = cached.get(assignment_name, None)

        timestamp_key = now()
        assignment_changed, assignment_data = check_assignment_updates(
            assignment, timestamp_key, assignment_cache)
        graders_finalized = assignment_data.pop('graders_finalized')
        data[assignment_name] = assignment_data

        if not assignment_changed:
            print('no change')
            continue

        changed = True
        print('assignment changed: sending notification')
        total = assignment_data['total']
        finalized = assignment_data['finalized']
        drafts = assignment_data['drafts']
        unclaimed = total - (finalized + drafts)
        if total == 0:
            done = 0
        else:
            done = finalized / total

        update_msg = _build_notification_msg(
            assignment=assignment_name,
            done=done,
            finalized=finalized,
            drafts=drafts,
            unclaimed=unclaimed,
            graders_finalized=graders_finalized)
        # the response doesn't matter
        _, error = send_slack_msg(slack_client, channel, update_msg)
        if error is not None:
            errors.append(_error('Slack API error: {}', error))

    return data, changed, errors


# ==============================================================================


def process_courses(slack_client, config, channels, cached):
    """Processes the assignments in the given courses and sends notifications to
    the specified Slack channel. Returns the new data to store, and a list of
    errors.
    """
    data = cached
    changed = False
    errors = []

    for course_period, course_info in config.items():
        print('processing course:', course_period)
        courses = codepost.course.list_available(name=course_info['course'],
                                                 period=course_info['period'])
        if len(courses) == 0:
            errors.append(
                _error('Course "{}" with period "{}" could not be found',
                       course_info['course'], course_info['period']))
            continue
        # take the first course if there are duplicates
        course = courses[0]
        course_cache = cached.get(course_period, None)
        course_data, course_changed, course_errors = \
            check_course_updates(
                slack_client,
                channels[course_info['channel']],
                course_period,
                course,
                course_info['assignments'],
                course_cache)
        if len(course_errors) > 0:
            errors += course_errors
        data[course_period] = course_data
        changed = changed or course_changed

    return data, changed, errors


# ==============================================================================


def read_cached_data(crypto, courses):
    """Reads the unencrypted cached data for the given courses."""

    data = {}

    if not CACHED_DATA_FOLDER.exists():
        return data, []

    for course_period in courses.keys():
        filepath = CACHED_DATA_FOLDER / (course_period + '.txt')
        if not filepath.exists():
            continue

        encoded_data_bytes = filepath.read_bytes()
        try:
            decoded_data_bytes = crypto.decrypt(encoded_data_bytes)
        except InvalidToken:
            # fail immediately: assume the same key was used for all the
            # saved data
            errors = [_error('Invalid decryption key for stored data')]
            return None, errors

        course_data = json.loads(decoded_data_bytes)
        data[course_period] = course_data

    return data, []


def write_data(crypto, data):
    """Writes the encrypted data to a file."""

    # ensure the folder exists
    CACHED_DATA_FOLDER.mkdir(parents=True, exist_ok=True)

    for course_period, course_data in data.items():
        filepath = CACHED_DATA_FOLDER / (course_period + '.txt')
        data_str = json.dumps(course_data)
        data_bytes = data_str.encode(encoding='utf-8')
        filepath.write_bytes(crypto.encrypt(data_bytes))


# ==============================================================================


def _validate_slack_channels(slack_client, channels):
    errors = []
    for channel, channel_id in channels.items():
        try:
            slack_client.chat_scheduledMessages_list(channel=channel_id)
        except SlackApiError as e:
            if not e.response:
                raise
            # assume e.response['ok'] = False
            reason = e.response.get('error', None)
            if reason is None:
                raise
            if reason == 'invalid_channel':
                # invalid channel id: {"ok": False, "error": "invalid_channel"}
                errors.append(
                    _error('Invalid id for Slack channel "{}"', channel))
            elif reason == 'not_in_channel':
                # not in channel: {"ok": False, "error": "not_in_channel"}
                errors.append(
                    _error('Slack key does not have access to channel "{}"',
                           channel))
            else:
                raise
    return errors


def _valid_date_range(start, end):
    if start is None and end is None:
        return True
    if start is None:
        return now_dt() < end
    if end is None:
        return start <= now_dt()
    return start <= now_dt() < end


def _validate_config_course(index, config_course):
    """Validates a course config dict.
    Returns a message and None if the course is invalid;
    otherwise, returns None and the course dict.
    """

    _invalid_msg = (
        f'Config file has an invalid course format at index {index}')

    def invalid(msg=None):
        if msg is None:
            msg = _invalid_msg
        return msg, None

    if not isinstance(config_course, dict):
        return invalid()

    course = {}

    # must be strs
    for key in ('course', 'period', 'channel'):
        if key not in config_course:
            return invalid()
        if not isinstance(config_course[key], str):
            return invalid()
        course[key] = config_course[key]

    if 'assignments' not in config_course:
        return invalid()
    if not isinstance(config_course['assignments'], list):
        return invalid()

    assignments = []
    for j, config_assignment in enumerate(config_course['assignments']):
        _invalid_assignment_msg = \
            _invalid_msg + f', assignment index {j}'
        if not isinstance(config_assignment, dict):
            return invalid(_invalid_assignment_msg)

        assignment = {}
        for key, required in (('name', True), ('start', False), ('end', False)):
            if key not in config_assignment:
                if not required:
                    assignment[key] = None
                    continue
                return invalid(_invalid_assignment_msg)
            if not isinstance(config_assignment[key], str):
                return invalid(_invalid_assignment_msg)
            assignment[key] = config_assignment[key]

        for key, delta in zip(('start', 'end'), (NO_DAY, ONE_DAY)):
            date_str = assignment[key]
            if date_str is None:
                continue
            try:
                date = datetime.strptime(date_str.strip(), DATE_FMT)
            except ValueError:
                return invalid(_invalid_assignment_msg +
                               ': invalid date format')
            date += delta
            # convert to eastern, then to utc
            date_utc = EASTERN_TZ.localize(date).astimezone(UTC_TZ)
            assignment[key] = date_utc

        assignment['valid_date_range'] = \
            _valid_date_range(assignment['start'], assignment['end'])

        assignments.append(assignment)

    course['assignments'] = assignments

    return None, course


def read_config_file(slack_client):
    """Reads the config file.
    Fails on invalid channel ids, missing required keys, unexpected types,
    repeated course name and period pairs, and unknown channel names.
    Returns the mapping of channels, the list of courses, and a list of errors.
    """
    errors = []

    INVALID_RETURN = None, None, errors

    if not CONFIG_FILE.exists():
        errors.append(_error('Config file "{}" does not exist', CONFIG_FILE))
        return INVALID_RETURN

    config = yaml.safe_load(CONFIG_FILE.read_text(encoding='utf-8'))

    # validate highest-level types
    if not isinstance(config, dict):
        errors.append(_error('Config file has an invalid format'))
        return INVALID_RETURN
    for value, expected in (
        (config.get('channels', None), dict),
        (config.get('sources', None), list),
    ):
        if not isinstance(value, expected):
            errors.append(_error('Config file has an invalid format'))
            return INVALID_RETURN

    # read channels
    channels = {}
    for channel, channel_id in config['channels'].items():
        if not isinstance(channel_id, str):
            errors.append(
                _error(
                    'Slack channels file has an invalid channel id for '
                    'channel "{}" (expected str)', channel))
            continue
        channels[channel] = channel_id
    errors += _validate_slack_channels(slack_client, channels)
    if len(errors) > 0:
        return INVALID_RETURN

    # read sources
    courses = {}
    for i, config_course in enumerate(config['sources']):
        invalid_msg, course = _validate_config_course(i, config_course)
        if invalid_msg is not None:
            errors.append(_error(invalid_msg))
            continue
        course_period = course['course'] + ' ' + course['period']
        if course_period in courses:
            errors.append(
                _error('Config file has a repeating course name and period'))
            continue
        if course['channel'] not in channels:
            errors.append(
                _error(
                    'Config file has unknown channel name "{}" '
                    'for course "{}"', course['channel'], course_period))
            continue
        courses[course_period] = course
    if len(errors) > 0:
        return INVALID_RETURN

    return channels, courses, errors


# ==============================================================================


def save_errors(errors):
    """Appends the given errors to the errors file."""
    if ERROR_LOGS_FILE.exists():
        existing_errors = ERROR_LOGS_FILE.read_text(encoding='utf-8')
    else:
        ERROR_LOGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        existing_errors = ''
    ERROR_LOGS_FILE.write_text(existing_errors + '\n'.join(errors) + '\n',
                               encoding='utf-8')


def main():
    errors = []

    # read environment variables
    secrets = {}
    for name in ('CODEPOST_API_KEY', 'SLACK_TOKEN', 'DECRYPTION_KEY'):
        secret = os.environ.get(name, None)
        if secret is None or secret == '':
            errors.append(
                _error('Environment variable "{}" could not be found', name))
        else:
            secrets[name] = secret

    if len(errors) > 0:
        save_errors(errors)
        return

    # configure the codePost key
    if not codepost.util.config.validate_api_key(secrets['CODEPOST_API_KEY']):
        errors.append(_error('codePost API key is invalid'))
    else:
        codepost.configure_api_key(secrets['CODEPOST_API_KEY'])

    # configure the slack token
    slack_client = WebClient(token=secrets['SLACK_TOKEN'])
    try:
        # validate the token using some random GET request
        slack_client.chat_scheduledMessages_list()
    except SlackApiError as e:
        # e.response should be: {"ok": False, "error": "invalid_auth"}
        if (e.response and not e.response.get('ok', False) and
                e.response.get('error', None) == 'invalid_auth'):
            errors.append(_error('Slack API token is invalid'))
        else:
            raise

    if len(errors) > 0:
        save_errors(errors)
        return

    channels, config, errors = read_config_file(slack_client)
    if len(errors) > 0:
        save_errors(errors)
        return

    # TODO: allow changing the key with `MultiFernet`
    # https://cryptography.io/en/latest/fernet/#cryptography.fernet.MultiFernet.rotate
    crypto = Fernet(secrets['DECRYPTION_KEY'])

    cached_data, errors = read_cached_data(crypto, config)
    if len(errors) > 0:
        save_errors(errors)
        return

    data, changed, errors = process_courses(slack_client, config, channels,
                                            cached_data)
    if len(errors) > 0:
        save_errors(errors)

    if changed:
        print('saving new data')
        write_data(crypto, data)


if __name__ == '__main__':
    try:
        main()
    except Exception as ex:  # pylint: disable=broad-except
        save_errors([_error('Uncaught error: {}', ex)])
        raise
