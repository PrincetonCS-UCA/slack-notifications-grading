# codePost/Slack notifications

This is a repository to download data from codePost and to trigger Slack
notifications in certain circumstances, based off of
[the `basic-git-scraper` template](https://github.com/jlumbroso/basic-git-scraper-template/).

## Summary

This repository uses a synchronized GitHub Action trigger to regularly check
codePost for certain changes to data (such as available submissions, graded
submissions, etc.), and to trigger a Slack notification if changes were found.

## Configuration

There should be a `config.yaml` file with the following format:

<!-- prettier-ignore -->
```yaml
# The slack channel ids
channels:
  "#126-grading-notifications": "ABC123"

# The message formats
messages:
  notification: "*{assignment}*: {done:.2%} done 
    ({finalized} finalized, {drafts} drafts, {unclaimed} left to grade)"
  recent_graders: "Graders who recently finalized: {graders}"
  deadline: "*{assignment}*: All submissions should now be finalized
    ({deadline} deadline)"

# The courses and assignments to send notifications for
sources:
  - course: "COS126"
    period: "F2022"
    channel: "#126-grading-notifications"
    assignments:
      - name: "Hello"
        start: "2022-09-14"
        end: "2022-09-18"
        deadline: "2022-09-16 17:00"
      - name: "Loops"
        start: "2022-09-21"
        end: "2022-09-25"
        deadline: "2022-09-23 17:00"
      - name: "NBody"
        start: "2022-09-28"
        end: "2022-10-02"
        deadline: "2022-09-30 17:00"
```

For each assignment, the `start` and `end` values defines a date range (with
possibly unbounded ends) for when that assignment should be processed. Dates
should be in the format `"YYYY-MM-DD"` and will be considered in Eastern Time.

The optional `deadline` value defines a time to send a notification that the
deadline has passed for all submissions to be finalized. The value should be in
the format `"YYYY-MM-DD HH:MM"` and will be considered in Eastern Time.

To customize the text of certain notification messages, define the `messages`
mapping. Each value should follow
[Python `str.format` specifications][str.format],
with possible variable replacements. The following keys are supported:

- `notification` (required): The notification message whenever an assignment has
  different numbers for total, unclaimed, drafts, or finalized submissions.
  Accepts the following variables:
  - `assignment`: The name of the assignment being processed.
  - `done`: The percentage of assignments finalized. Note this number is between
    0 and 1, so it is recommended to use the `{done:.2%}` format to format it as
    a percentage.
  - `total`: The total number of submissions.
  - `finalized`: The number of finalized submissions.
  - `drafts`: The number of drafts.
  - `unclaimed`: The number of unclaimed submissions.
- `recent_graders`: A message to attach after `notification` listing the graders
  who have finalized a submission since the last run. Only applies if the number
  of graders is greater than 0. Accepts the following variables:
  - `graders`: A comma separated list of all the graders in backticks.
- `deadline`: The notification message for when the deadline for an assignment
  is passed. Accepts the following variables:
  - `assignment`: The name of the assignment being processed.
  - `deadline`: The specified deadline in the config file for this assignment.

## Deterministic Finite Automaton

- Read from `data/*`,
- Fetch recent data from codePost,
- Compare to cached data,
- If there are changes, trigger a Slack notification,
- Update the cached data.

## References

- `utas_slack_bot.py` is the bot that is used to send Slack notifications based
  on updated data from codePost.
- This is how to store both the codePost key and the Slack OAuth key securely in
  the repository to use them in the GitHub Actions flow:
  https://docs.github.com/en/actions/security-guides/encrypted-secrets.

## Local development

- Clone repository:
  `git clone https://github.com/PrincetonCS-UCA/slack-notifications-grading.git`
- Install the dependencies in a virtual environment: `pipenv install`
- Run in the venv: `pipenv run python utas_slack_bot.py`

[str.format]: https://docs.python.org/3/library/string.html#formatstrings
