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
from slack_sdk.errors import SlackApiError

import read_config
from utils import _error, _try_format, get_slack_client, now, validate_codepost

# ==============================================================================

# The cached data is separated into one encrypted txt file for each course
CACHED_DATA_FOLDER = Path('./data')

ERROR_LOGS_FILE = CACHED_DATA_FOLDER / '_ERRORS.txt'

IGNORE_GRADER_PREFIX = [
    # difficult or bad submissions; being held for a reason
    'jdlou+',
]

# ==============================================================================


def _get_assignment_stats(assignment_data):
    total = assignment_data['total']
    finalized = assignment_data['finalized']
    if total == 0:
        done = 0
    else:
        done = finalized / total
    return {
        'done': done,
        'total': total,
        'finalized': finalized,
        'drafts': assignment_data['drafts'],
        'unclaimed': assignment_data['unclaimed'],
    }


def _build_deadline_msg(messages, assignment_info, assignment_data):
    """Builds a deadline message.
    Returns the message and None, or None and an error message if there was an
    error.
    """
    deadline_msg, error = _try_format(messages['deadline'],
                                      assignment=assignment_info['name'],
                                      deadline=assignment_info['deadline'],
                                      **_get_assignment_stats(assignment_data))
    if error is not None:
        return None, _error('Error while building deadline message: {}', error)
    return deadline_msg, None


def _build_notification_msg(messages, assignment_name, assignment_data,
                            graders_finalized):
    """Builds a notification message.
    Returns the message and None, or None and an error message if there was an
    error.
    """
    notification_msg, error = _try_format(
        messages['notification'],
        assignment=assignment_name,
        **_get_assignment_stats(assignment_data))
    if error is not None:
        return None, _error('Error while building notification message: {}',
                            error)

    if 'recent_graders' in messages and len(graders_finalized) > 0:
        graders = []
        for grader in graders_finalized:
            # remove "@princeton.edu"
            grader = grader[:-len('@princeton.edu')]
            # put in backticks
            graders.append(f'`{grader}`')
        graders_str = ', '.join(graders)
        recent_graders_msg, error = _try_format(messages['recent_graders'],
                                                graders=graders_str)
        if error is not None:
            return None, _error(
                'Error while building recent graders message: {}', error)
        notification_msg += '\n' + recent_graders_msg

    return notification_msg, None


# ==============================================================================


def send_slack_msg(slack_client, channel_id, msg, as_block=False):
    """Sends a message on Slack to the specified channel. If `as_block` is True,
    the message is sent as a markdown block.

    Returns the request response, and the error response or None if there was no
    error.
    """

    print('sending message to channel:', channel_id)
    print(msg)

    kwargs = {'channel': channel_id}
    if as_block:
        kwargs['blocks'] = [{
            'type': 'section',
            'text': {
                'type': 'mrkdwn',
                'text': msg,
            },
        }]
    else:
        kwargs['text'] = msg

    try:
        response = slack_client.chat_postMessage(**kwargs)
        error = None
    except SlackApiError as e:
        response = None
        error = e.response

    return response, error


# ==============================================================================


def check_assignment_updates(assignment, timestamp_key, cached=None):
    """Checks the codePost assignment for updates, comparing to the cached data.
    Returns whether to save new data, whether to send a notification, and the
    new data to store for this assignment.
    """

    def max_int_num(nums, **kwargs):
        """Returns the maximum number of string numbers."""
        args = {}
        if 'default' in kwargs:
            args['default'] = kwargs['default']
        return max(nums, key=lambda a: (len(a), tuple(a)), **args)

    sent_deadline_message = None
    # maps: run index -> timestamp
    runs = {}
    index = 1
    # maps: submission id -> run index ->
    #   status of "unclaimed", "draft", "finalized", or "deleted"
    submissions = {}
    deleted = set()
    if cached is not None:
        sent_deadline_message = cached.get('sent_deadline_message',
                                           sent_deadline_message)
        runs = cached.get('runs', runs)
        index = int(max_int_num(runs.keys(), default=0)) + 1
        submissions = cached.get('submissions', submissions)
        deleted = set(submissions.keys())
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
            for prefix in IGNORE_GRADER_PREFIX:
                if submission.grader.startswith(prefix):
                    # ignore
                    num_total -= 1
                    break
            else:
                # don't ignore
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
        'sent_deadline_message': sent_deadline_message,
        'runs': runs,
        'submissions': submissions,
        'graders_finalized': graders_finalized,
    }

    if cached is None:
        # first time getting data
        changed = True
    elif updated_status:
        changed = True
    else:
        changed = any(
            cached.get(key, None) != data[key]
            for key in ('total', 'finalized', 'drafts', 'unclaimed'))

    send_notif = changed
    if num_total == 0 or num_finalized == 0:
        # no submissions uploaded or no submissions finalized: no need to send
        # notification
        send_notif = False

    return changed, send_notif, data


# ==============================================================================


def check_course_updates(slack_client,
                         channel,
                         messages,
                         course_period,
                         course,
                         assignments,
                         cached=None):
    """Checks the codePost course for updates, comparing to the cached data.
    Returns the new data to store for this course, whether the data changed, and
    a list of errors.
    """
    if cached is None:
        cached = {}

    data = cached
    changed = False
    errors = []

    course_assignments = {a.name: a for a in course.assignments}

    for assignment_info in assignments:
        assignment_name = assignment_info['name']
        print('processing assignment:', assignment_name)
        if not assignment_info['valid_date_range']:
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
        assignment_changed, send_notif, assignment_data = (
            check_assignment_updates(assignment, timestamp_key,
                                     assignment_cache))
        graders_finalized = assignment_data.pop('graders_finalized')
        data[assignment_name] = assignment_data
        if assignment_changed:
            changed = True

        # if the deadline message has not been sent but the assignment passed
        # its deadline, send the deadline message
        if (assignment_data['sent_deadline_message'] is None and
                assignment_info.get('passed_deadline', False)):
            deadline_msg, error = _build_deadline_msg(messages, assignment_info,
                                                      assignment_data)
            if error is not None:
                errors.append(error)
            else:
                print('passed deadline: sending message')
                _, error = send_slack_msg(slack_client, channel, deadline_msg)
                if error is not None:
                    errors.append(_error('Slack API error: {}', error))
                else:
                    # if a deadline message hasn't been sent before, need to
                    # update cached data so that the message doesn't get sent
                    # again
                    assignment_data['sent_deadline_message'] = now()
                    changed = True

        if not send_notif:
            if assignment_changed:
                print('data changed, but ', end='')
            print('not sending notification')
            continue

        changed = True
        print('assignment changed: sending notification')
        update_msg, error = _build_notification_msg(messages, assignment_name,
                                                    assignment_data,
                                                    graders_finalized)
        if error is not None:
            errors.append(error)
        else:
            # the response doesn't matter
            _, error = send_slack_msg(slack_client, channel, update_msg)
            if error is not None:
                errors.append(_error('Slack API error: {}', error))

    return data, changed, errors


# ==============================================================================


def process_courses(slack_client, config, channels, messages, cached):
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
                messages,
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

    channels, messages, config, errors = read_config.read(slack_client)
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
                                            messages, cached_data)
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
