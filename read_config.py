"""
read_config.py
Reads the `config.yaml` file.

This file can be run to validate the codePost courses in the file.
"""

# ==============================================================================

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import codepost
import pytz
import yaml
from slack_sdk.errors import SlackApiError

from utils import _error, get_slack_client, now_dt, validate_codepost

# ==============================================================================

__all__ = ('read',)

# ==============================================================================

CONFIG_FILE = Path('config.yaml')

UTC_TZ = pytz.utc
EASTERN_TZ = pytz.timezone('US/Eastern')
DATE_FMT = '%Y-%m-%d'
DEADLINE_FMT = '%Y-%m-%d %H:%M'

NO_DAY = timedelta()
ONE_DAY = timedelta(days=1)

# ==============================================================================

# the support messages and the required keys
MESSAGES_KWARGS = {
    'deadline': {
        'assignment': 'test',
        'deadline': 'test'
    },
}

# ==============================================================================


def _validate_slack_channels(slack_client, channels, fmt_error):
    errors = []
    for channel, channel_id in channels.items():
        try:
            # https://api.slack.com/methods/conversations.info
            slack_client.conversations_info(channel=channel_id)
        except SlackApiError as e:
            if not e.response or e.reponse.get('ok', None) is not False:
                raise
            reason = e.response.get('error', None)
            if reason is None:
                raise
            if reason == 'channel_not_found':
                errors.append(
                    fmt_error('Invalid id for Slack channel "{}"', channel))
            elif reason == 'missing_scope':
                errors.append(
                    fmt_error('Slack key does not have access to channel "{}"',
                              channel))
            else:
                raise
    return errors


def _eastern_to_utc(dt):
    return EASTERN_TZ.localize(dt).astimezone(UTC_TZ)


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

    _invalid_msg = f'Config file has an invalid course format at index {index}'

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

    # assignments
    if 'assignments' not in config_course:
        return invalid()
    if not isinstance(config_course['assignments'], list):
        return invalid()

    assignments = []
    has_deadline = False
    for j, config_assignment in enumerate(config_course['assignments']):
        _invalid_assignment_msg = _invalid_msg + f', assignment index {j}'
        if not isinstance(config_assignment, dict):
            return invalid(_invalid_assignment_msg)

        assignment = {}
        for key, required in (
            ('name', True),
            ('start', False),
            ('end', False),
            ('deadline', False),
        ):
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
            assignment[key] = _eastern_to_utc(date)

        assignment['valid_date_range'] = \
            _valid_date_range(assignment['start'], assignment['end'])

        deadline = assignment['deadline']
        if deadline is not None:
            deadline = deadline.strip()
            assignment['deadline'] = deadline
            has_deadline = True
            try:
                deadline_dt = datetime.strptime(deadline.strip(), DEADLINE_FMT)
            except ValueError:
                return invalid(_invalid_assignment_msg +
                               ': invalid deadline format')
            deadline_utc = _eastern_to_utc(deadline_dt)
            assignment['passed_deadline'] = deadline_utc >= now_dt()

        assignments.append(assignment)

    course['assignments'] = assignments
    course['has_deadline'] = has_deadline

    return None, course


def read(slack_client, fmt_error=_error):
    """Reads the config file.
    Fails on invalid channel ids, missing required keys, unexpected types,
    repeated course name and period pairs, and unknown channel names.
    Returns the mapping of channels, the mapping of messages, the list of
    courses, and a list of errors.
    """
    errors = []

    INVALID_RETURN = None, None, None, errors

    if not CONFIG_FILE.exists():
        errors.append(fmt_error('Config file "{}" does not exist', CONFIG_FILE))
        return INVALID_RETURN

    config = yaml.safe_load(CONFIG_FILE.read_text(encoding='utf-8'))

    # validate highest-level types
    if not isinstance(config, dict):
        errors.append(
            fmt_error('Config file has an invalid format (expected dict)'))
        return INVALID_RETURN

    for key, default, expected in (
        ('channels', None, dict),
        ('messages', {}, dict),
        ('sources', None, list),
    ):
        if not isinstance(config.get(key, default), expected):
            errors.append(
                fmt_error(
                    'Config file has an invalid format: key "{}" '
                    'expected to have type {}', key, expected.__name__))
    if len(errors) > 0:
        return INVALID_RETURN

    # read channels
    channels = {}
    for channel, channel_id in config['channels'].items():
        if not isinstance(channel_id, str):
            errors.append(
                fmt_error('Invalid channel id for channel "{}" (expected str)',
                          channel))
            continue
        channels[channel] = channel_id
    errors += _validate_slack_channels(slack_client, channels, fmt_error)
    if len(errors) > 0:
        return INVALID_RETURN

    # read messages
    messages = {}
    for key, value in config.get('messages', {}).items():
        if not isinstance(value, str):
            errors.append(
                fmt_error('Invalid message for key "{}" (expected str)', key))
            continue
        messages[key] = value
    if len(errors) > 0:
        return INVALID_RETURN
    # validate messages
    for key, kwargs in MESSAGES_KWARGS.items():
        if key not in messages:
            continue
        message = messages[key].strip()
        if message == '':
            errors.append(fmt_error('Empty message str for key "{}"', key))
            continue
        messages[key] = message
        try:
            message.format(**kwargs)
        except (IndexError, KeyError, ValueError) as e:
            errors.append(
                fmt_error(
                    'Invalid message str for key "{}" '
                    '(supported variable keys are: {}): {}: {}', key,
                    ', '.join(f'"{var_key}"' for var_key in kwargs),
                    e.__class__.__name__, e))
    if len(errors) > 0:
        return INVALID_RETURN

    # read sources
    courses = {}
    has_deadline = False
    for i, config_course in enumerate(config['sources']):
        invalid_msg, course = _validate_config_course(i, config_course)
        if invalid_msg is not None:
            errors.append(fmt_error(invalid_msg))
            continue
        course_period = course['course'] + ' ' + course['period']
        if course_period in courses:
            errors.append(
                fmt_error('Config file has a repeating course name and period'))
            continue
        if course['channel'] not in channels:
            errors.append(
                fmt_error(
                    'Config file has unknown channel name "{}" for course "{}"',
                    course['channel'], course_period))
            continue
        if course.pop('has_deadline'):
            has_deadline = True
        courses[course_period] = course
    if len(errors) > 0:
        return INVALID_RETURN

    if has_deadline and 'deadline' not in messages:
        errors.append(
            fmt_error(
                'Deadlines given in assignments, but missing deadline message'))
        return INVALID_RETURN

    return channels, messages, courses, errors


# ==============================================================================


def check(success):
    if not success:
        sys.exit(1)


def validate():
    """Reads the config file and validates the codePost courses and assignments.
    """
    failed = False

    # read environment variables
    secrets = {}
    for name in ('CODEPOST_API_KEY', 'SLACK_TOKEN'):
        secret = os.environ.get(name, None)
        if secret is None or secret == '':
            failed = True
            print(f'Environment variable "{name}" could not be found')
            continue
        secrets[name] = secret

    check(not failed)

    success = validate_codepost(secrets['CODEPOST_API_KEY'])
    if not success:
        failed = True
        print('codePost API key is invalid')

    success, slack_client = get_slack_client(secrets['SLACK_TOKEN'])
    if not success:
        failed = True
        print('Slack API token is invalid')

    check(not failed)

    def fmt_error(msg, *args, **kwargs):
        print(msg.format(*args, **kwargs))
        return 0

    _, _, config, errors = read(slack_client, fmt_error=fmt_error)
    check(len(errors) == 0)

    # validate all codePost objects
    courses = {}
    repeated = set()
    for course in codepost.course.list_available():
        course_period = course.name + ' ' + course.period
        if course_period not in courses:
            # use the first course if there are duplicates
            courses[course_period] = course
        elif course_period not in repeated:
            print('Warning: there are multiple courses with the name '
                  f'"{course.name}" and period "{course.period}"')
            repeated.add(course_period)
    for course_period, course_data in config.items():
        if course_period not in courses:
            failed = True
            print(f'Course "{course_period}" could not be found')
            continue
        course = courses[course_period]
        assignments = {assignment.name for assignment in course.assignments}
        for assignment_data in course_data['assignments']:
            assignment_name = assignment_data['name']
            if assignment_name not in assignments:
                failed = True
                print(f'Course "{course_period}" does not have an assignment '
                      f'"{assignment_name}"')

    check(not failed)

    print('Passed')


if __name__ == '__main__':
    validate()
