"""
read_cached_data.py
A debugger to read the decrypted cached data.
"""

# ======================================================================

import json
import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

# ======================================================================

# yapf: disable
HELP_USAGE = (
    f'Usage: python {Path(__file__).name} FILE [OUTPUT_FILE]\n'
    '\n'
    '  Read, unencrypt, and output the given file.'
)
# yapf: enable

# ======================================================================


def main():
    # The first arg is this filename
    _, *args = sys.argv
    if len(args) == 0 or any(arg in ('-h', '--help') for arg in args):
        print(HELP_USAGE)
        return

    filepath = Path(args[0])
    if not filepath.exists:
        print(f'Error: file "{filepath}" does not exist')
        return

    decryption_key = os.environ.get('DECRYPTION_KEY', None)
    if decryption_key is None:
        print('Cannot find decryption key')
        return

    crypto = Fernet(decryption_key)

    encoded_data_bytes = filepath.read_bytes()
    try:
        decoded_data_bytes = crypto.decrypt(encoded_data_bytes)
    except InvalidToken:
        print('Error: Invalid decryption key used for stored data')
        return

    data = json.loads(decoded_data_bytes)
    data_str = json.dumps(data, indent=2)

    if len(args) >= 2:
        output_filepath = Path(args[1])
        output_filepath.write_text(data_str, encoding='utf-8')
    else:
        # print it nicely
        print(data_str)


if __name__ == '__main__':
    main()
