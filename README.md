# codePost/Slack notifications

This is a repository to download data from codePost and to trigger Slack notifications in certain circumstances, based off of [the `basic-git-scraper` template](https://github.com/jlumbroso/basic-git-scraper-template/).

## Summary

This repository should use a synchronized GitHub Action trigger, to regularly check codePost for certain changes to data, and if changes to data have transpired (changed of available submissions, or graded submissions, etc.), then it should trigger a Slack notification.

- **Configuration:** There should be a `config.yaml` file that takes: The list of (course, period) pairs of codePost courses to monitor. And maybe also the messages to post in Slack (a jinja2 template in the YAML file for instance), and the channels to be posted to. Actually, maybe you want each course to be in a different channel, so maybe a syntax like this (you think about this):
```yaml
sources:
- course: COS126
  period: F2022
  channel: "#126-grading-notifications"
  assignments: ["Hello", "Loops", "Recursion"]
  monitor: ["available", "graded"]
  template:
  - available: "{{ available }} submissions are available for {{ assignment }}!"
  - graded: "{{ graded }} submissions for {{ assignment }} have been graded!"
```
and maybe have the ability to have multiple blocks. The last template property is just an example. You can think about this, but the point is there should be a way to customize the Slack messages, and some standard variables should be available to the template, like `available`, `graded`, `assignment`, etc.

- **Deterministic Finite Automaton:**
  - Read from `data/*.json`,
  - Fetch recent data from codePost,
  - Compare to cached data,
  - If there are changes, then trigger a Slack notification,
  - Update the cached data.

- **Idea:** Maybe instead of having cached data, there could be a **log of the messages that have been sent**, and it is this log that is used to determine if a message should be retriggered. The advantage of this approach: It does not leak information, and the log is also useful for general diagnostics.

## References

- `utas_slack_bot.py` is the bot that used to send Slack notifications from webhooks.
- This is how to store both the codePost key and the Slack OAuth key securely in the repository: https://docs.github.com/en/actions/security-guides/encrypted-secrets and how to use it in the GitHub Actions flow. At the bot level, you will read it from the environment.
- In the `.github/workflows/scrape.yaml`, the last action is to commit any `./data/*.json` files that have changed. So you can output in that folder any state that you would like to recuperate on the next call.

## Local development

- Clone repository: `git clone https://github.com/PrincetonCS-UCA/slack-grading-notifications/`
- Install the dependencies in a virtual environment: `pipenv install`
- Run in the venv: `pipenv run python script.py`
