"""
utils.py
Shared methods and utilities.
"""

# ==============================================================================

from datetime import datetime

import codepost
import pytz
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# ==============================================================================

__all__ = (
    'now_dt',
    'now',
    '_error',
)

# ==============================================================================

UTC_TZ = pytz.utc
EASTERN_TZ = pytz.timezone('US/Eastern')

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


# ==============================================================================


def _error(msg, *args, **kwargs):
    """Returns the formatted message with the current timestamp."""
    formatted = msg.format(*args, **kwargs)
    print('Error:', formatted)
    return f'[{now()}] {formatted}'


# ==============================================================================


def validate_codepost(codepost_api_key):
    """Validates the given codePost API key."""
    if not codepost.util.config.validate_api_key(codepost_api_key):
        return False
    codepost.configure_api_key(codepost_api_key)
    return True


def get_slack_client(slack_token):
    """Gets the slack client using the given token.
    Returns whether True and the client or False and None.
    """
    slack_client = WebClient(token=slack_token)
    try:
        # validate the token
        # https://api.slack.com/methods/auth.test
        slack_client.api_test()
    except SlackApiError as e:
        if e.response['error'] == 'invalid_auth':
            return False, None
        raise
    return True, slack_client
