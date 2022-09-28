"""
utas_slack_bot.py
This script sends update notifications to the UTAs slack on the progress
of grading from codePost.
"""

# ======================================================================

import json
import os
from datetime import datetime
from pathlib import Path

import codepost
import yaml
from cryptography.fernet import Fernet, InvalidToken
from slack import WebClient
from slack.errors import SlackApiError

# ======================================================================

# The cached data is separated into one folder for each course,
# then one file for each time the course's data is updated
CACHED_DATA_FOLDER = Path('./data')

ERROR_LOGS_FILE = CACHED_DATA_FOLDER / 'ERRORS.txt'

CONFIG_FILE = Path('config.yaml')
SLACK_CHANNELS_FILE = Path('channels.yaml')

UPDATE_MESSAGE_TEMPLATE = (
    '*{assignment}*: {done:.2%} done ' +
    '({finalized} finalized, {drafts} drafts, ' +
    '{unclaimed} left to grade)'
    # FIXME: add support for this
    # 'Graders who most recently finalized: {graders_finalized}'
)

# ======================================================================


def now(filename=False):
    """Returns the current time in UTC as a string.

    Since UTC does not observe daylight savings, this means that each
    call to this function (with `filename`=False) should theoretically
    return something different.
    """
    if filename:
        fmt = '%Y-%m-%d %H%M%S'
    else:
        fmt = '%Y-%m-%d %H:%M:%S.%f'
    return datetime.utcnow().strftime(fmt)


def _error(msg, *args, **kwargs):
    """Returns the formatted message with the current timestamp."""
    formatted = msg.format(*args, **kwargs)
    print('Error:', formatted)
    return f'[{now()}] {formatted}'

# ======================================================================


def send_slack_msg(slack_client, channel_id, msg, as_block=False):
    """Sends a message on Slack to the specified channel. If `as_block`
    is True, the message is sent as a markdown block.

    Returns the request response, and the error response or None if
    there was no error.
    """

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

# ======================================================================


def check_assignment_updates(assignment, cached=None):
    """Checks the codePost assignment for updates, comparing to the
    cached data. Returns whether there were updates and the new data to
    store for this assignment.
    """

    num_total = 0
    num_finalized = 0
    num_drafts = 0
    num_unclaimed = 0
    # maps: submission id -> timestamp ->
    #   status of "unclaimed", "draft", or "finalized"
    submissions = {}
    if cached is not None and 'submissions' in cached:
        submissions = cached['submissions']

    # get info about each submission
    for submission in assignment.list_submissions():
        num_total += 1
        if submission.isFinalized:
            num_finalized += 1
            status = 'finalized'
        elif submission.grader is not None:
            num_drafts += 1
            status = 'draft'
        else:
            num_unclaimed += 1
            status = 'unclaimed'

        if submission.id not in submissions:
            submissions[submission.id] = {}
        submission_data = submissions[submission.id]

        timestamp = now()
        if timestamp in submission_data:
            # impossible, but don't want to overwrite data
            i = 1
            new_timestamp = f'{timestamp} {i}'
            while new_timestamp in submission_data:
                i += 1
                new_timestamp = f'{timestamp} {i}'
            timestamp = new_timestamp
        submission_data[timestamp] = {
            'status': status,
            'grader': submission.grader,
        }

    data = {
        'total': num_total,
        'finalized': num_finalized,
        'drafts': num_drafts,
        'unclaimed': num_unclaimed,
        'submissions': submissions,
    }

    if num_total == 0:
        changed = False
    else:
        changed = cached is None or any(
            cached.get(key, None) != data[key] for key in
            ('total', 'finalized', 'drafts', 'unclaimed')
        )

    return changed, data

# ======================================================================


def check_course_updates(
        slack_client, channel,
        course_period, course, assignments, cached=None):
    """Checks the codePost course for updates, comparing to the cached
    data. Returns the new data to store for this course, and a list of
    errors.
    """
    if cached is None:
        cached = {}

    data = {}
    errors = []

    course_assignments = {a.name: a for a in course.assignments}

    for assignment_name in assignments:
        print('processing assignment:', assignment_name)
        if assignment_name not in course_assignments:
            errors.append(_error(
                'Course "{}" does not have an assignment called "{}"',
                course_period, assignment_name))
            continue
        assignment = course_assignments[assignment_name]
        assignment_cache = cached.get(assignment_name, None)

        changed, assignment_data = check_assignment_updates(
            assignment, assignment_cache)
        data[assignment_name] = assignment_data

        if not changed:
            continue

        print('assignment changed: sending notification')
        total = assignment_data['total']
        finalized = assignment_data['finalized']
        drafts = assignment_data['drafts']
        unclaimed = total - (finalized + drafts)
        if total == 0:
            done = 0
        else:
            done = finalized / total

        update_msg = UPDATE_MESSAGE_TEMPLATE.format(
            assignment=assignment_name, done=done,
            finalized=finalized, drafts=drafts, unclaimed=unclaimed,
            graders_finalized=''
        )
        # the response doesn't matter
        _, error = send_slack_msg(
            slack_client, channel, update_msg)
        if error is not None:
            errors.append(_error('Slack API error: {}', error))

    return data, errors

# ======================================================================


def process_courses(slack_client, courses, channels, cached):
    """Processes the assignments in the given courses and sends
    notifications to the specified Slack channel. Returns the new data
    to store, and a list of errors.
    """
    data = {}
    errors = []

    for course_period, course_info in courses.items():
        print('processing course:', course_period)
        courses = codepost.course.list_available(
            name=course_info['course'], period=course_info['period'])
        if len(courses) == 0:
            errors.append(_error(
                'Course "{}" with period "{}" could not be found',
                course_info['course'], course_info['period']))
            continue
        # take the first course if there are duplicates
        course = courses[0]
        course_cache = cached.get(course_period, None)
        course_data, course_errors = check_course_updates(
            slack_client,
            channels[course_info['channel']],
            course_period,
            course,
            course_info['assignments'],
            course_cache)
        if len(course_errors) > 0:
            errors += course_errors
        data[course_period] = course_data

    return data, errors

# ======================================================================


def read_cached_data(crypto, courses):
    """Reads the unencrypted cached data for the given courses."""

    # ensure the folder exists
    CACHED_DATA_FOLDER.mkdir(parents=True, exist_ok=True)

    data = {}

    for course_period in courses.keys():
        course_folder = CACHED_DATA_FOLDER / course_period
        if not course_folder.exists():
            continue

        # each file is named by the timestamp it was created, so look
        # for the latest one
        filepath = max(course_folder.glob('*'), default=None, key=str)
        if filepath is None:
            # no files
            continue

        encoded_data_bytes = filepath.read_bytes()
        try:
            decoded_data_bytes = crypto.decrypt(encoded_data_bytes)
        except InvalidToken:
            # fail immediately: assume the same key was used for all
            # the saved data
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
        filename = now(filename=True) + '.txt'
        course_folder = CACHED_DATA_FOLDER / course_period
        # create the folder if it doesn't exist
        course_folder.mkdir(parents=True, exist_ok=True)
        filepath = course_folder / filename

        data_str = json.dumps(course_data)
        data_bytes = data_str.encode(encoding='utf-8')
        filepath.write_bytes(crypto.encrypt(data_bytes))

# ======================================================================


def read_slack_channels_file(slack_client):
    """Reads the Slack channels file.
    Validates all the channel ids.
    Returns the dict of channels, and a list of errors.
    """
    errors = []

    if not SLACK_CHANNELS_FILE.exists():
        errors.append(_error(
            'Slack channels file "{}" does not exist',
            SLACK_CHANNELS_FILE))
        return None, errors

    channels = yaml.safe_load(
        SLACK_CHANNELS_FILE.read_text(encoding='utf-8'))

    # validate channels
    if not isinstance(channels, dict):
        errors.append(_error(
            'Slack channels file has an invalid format'))
        return None, errors

    for channel, channel_id in channels.items():
        if not isinstance(channel_id, str):
            errors.append(_error(
                'Slack channels file has an invalid channel id for '
                'channel "{}" (expected str)',
                channel))
            continue
        try:
            slack_client.chat_scheduledMessages_list(channel=channel_id)
        except SlackApiError as e:
            # e.response should be:
            # {"ok": False, "error": "invalid_channel"}
            if (e.response and
                not e.response.get('ok', False) and
                    e.response.get('error', None) == 'invalid_channel'):
                errors.append(_error(
                    'Invalid id for Slack channel "{}"', channel))
            else:
                raise

    return channels, errors


def read_config_file(channels):
    """Reads the config file.
    Fails on missing required keys, unexpected types, repeated course
    name and period pairs, and unknown channel names.
    Returns the list of courses, and a list of errors.
    """
    errors = []

    if not CONFIG_FILE.exists():
        errors.append(_error(
            'Config file "{}" does not exist', CONFIG_FILE))
        return None, errors

    config = yaml.safe_load(CONFIG_FILE.read_text(encoding='utf-8'))

    # validate config
    if (not isinstance(config, dict) or
            not isinstance(config.get('sources', None), list)):
        errors.append(_error('Config file has an invalid format'))
        return None, errors

    courses = {}
    for i, course_config in enumerate(config['sources']):
        invalid = False
        course = {}
        for key in ('course', 'period', 'channel'):
            if key not in course_config:
                invalid = True
                continue
            if not isinstance(course_config[key], str):
                invalid = True
                continue
            course[key] = course_config[key]
        for key in ('assignments',):
            if key not in course_config:
                invalid = True
                continue
            if not isinstance(course_config[key], list):
                invalid = True
                continue
            for val in course_config[key]:
                if not isinstance(val, str):
                    invalid = True
                    break
            course[key] = course_config[key]
        if invalid:
            errors.append(_error(
                'Config file has an invalid course format at index {}',
                i))
            continue
        course_period = course['course'] + ' ' + course['period']
        if course_period in courses:
            errors.append(_error(
                'Config file has a repeating course name and period'))
            continue
        if course['channel'] not in channels:
            errors.append(_error(
                'Config file has unknown channel name "{}" '
                'for course "{}"',
                course['channel'], course_period))
            continue
        courses[course_period] = course

    return courses, errors

# ======================================================================


def save_errors(errors):
    """Appends the given errors to the errors file."""
    if ERROR_LOGS_FILE.exists():
        existing_errors = ERROR_LOGS_FILE.read_text(encoding='utf-8')
    else:
        ERROR_LOGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        existing_errors = ''
    ERROR_LOGS_FILE.write_text(
        existing_errors + '\n'.join(errors) + '\n',
        encoding='utf-8')


def main():
    errors = []

    # read environment variables
    secrets = {}
    for name in ('CODEPOST_API_KEY', 'SLACK_TOKEN', 'DECRYPTION_KEY'):
        secret = os.environ.get(name, None)
        if secret is None or secret == '':
            errors.append(_error(
                'Environment variable for "{}" could not be found',
                name))
        else:
            secrets[name] = secret

    if len(errors) > 0:
        save_errors(errors)
        return

    # configure the codePost key
    if not codepost.util.config.validate_api_key(
            secrets['CODEPOST_API_KEY']):
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
        if (e.response and
            not e.response.get('ok', False) and
                e.response.get('error', None) == 'invalid_auth'):
            errors.append(_error('Slack API token is invalid'))
        else:
            raise

    if len(errors) > 0:
        save_errors(errors)
        return

    channels, errors = read_slack_channels_file(slack_client)
    if len(errors) > 0:
        save_errors(errors)
        return

    config, errors = read_config_file(channels)
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

    data, errors = process_courses(
        slack_client, config, channels, cached_data)
    if len(errors) > 0:
        save_errors(errors)

    write_data(crypto, data)


if __name__ == '__main__':
    try:
        main()
    except Exception as ex:  # pylint: disable=broad-except
        save_errors([_error('Uncaught error: {}', ex)])
        raise
