"""
utas_slack_bot.py
This script sends update notifications to the UTAs slack on the progress
of grading from codePost.
"""

# ==============================================================================

import json
import os
from pathlib import Path

import codepost
from cryptography.fernet import Fernet, InvalidToken
from slack.errors import SlackApiError

import read_config
from utils import _error, get_slack_client, now, validate_codepost

# ==============================================================================

# The cached data is separated into one encrypted txt file for each course
CACHED_DATA_FOLDER = Path('./data')

ERROR_LOGS_FILE = CACHED_DATA_FOLDER / '_ERRORS.txt'

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

    if num_total == 0 or num_finalized == 0:
        # no submissions uploaded or no submissions finalized: no need to send
        # notification
        changed = False
    elif cached is None:
        # first time getting data
        changed = True
    elif updated_status:
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
    data = {}
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
        if course_changed:
            changed = True
            data[course_period] = course_data

    return data, changed, errors


# ==============================================================================


def _get_course_filepath(course_period):
    # very minimal attempt to slugify: remove spaces
    filename = course_period.replace(' ', '_') + '.txt'
    return CACHED_DATA_FOLDER / filename


def read_cached_data(crypto, courses):
    """Reads the unencrypted cached data for the given courses."""

    data = {}

    if not CACHED_DATA_FOLDER.exists():
        return data, []

    for course_period in courses.keys():
        filepath = _get_course_filepath(course_period)
        if not filepath.exists():
            continue

        encoded_data_bytes = filepath.read_bytes()
        try:
            decoded_data_bytes = crypto.decrypt(encoded_data_bytes)
        except InvalidToken:
            # fail immediately: assume the same key was used for all the saved
            # data
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
        filepath = _get_course_filepath(course_period)
        data_str = json.dumps(course_data)
        data_bytes = data_str.encode(encoding='utf-8')
        filepath.write_bytes(crypto.encrypt(data_bytes))


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
    print('Current time:', now())

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

    success = validate_codepost(secrets['CODEPOST_API_KEY'])
    if not success:
        errors.append(_error('codePost API key is invalid'))

    success, slack_client = get_slack_client(secrets['SLACK_TOKEN'])
    if not success:
        errors.append(_error('Slack API token is invalid'))

    if len(errors) > 0:
        save_errors(errors)
        return

    channels, config, errors = read_config.read(slack_client)
    if len(errors) > 0:
        save_errors(errors)
        return

    # TODO: allow changing the key with `MultiFernet`
    # https://cryptography.io/en/latest/fernet/#cryptography.fernet.MultiFernet.rotate
    try:
        crypto = Fernet(secrets['DECRYPTION_KEY'])
    except ValueError:
        save_errors([_error('Invalid decryption key for stored data')])
        return

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
