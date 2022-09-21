# codePost/Slack notifications

This is a repository to download data from codePost and to trigger Slack
notifications in certain circumstances, based off of
[the `basic-git-scraper` template](https://github.com/jlumbroso/basic-git-scraper-template/).

## Summary

This repository uses a synchronized GitHub Action trigger to regularly check
codePost for certain changes to data (such as available submissions, graded
submissions, etc.), and to trigger a Slack notification if changes were found.

- **Configuration:** There should be a `config.yaml` file with the following
  format:

  <!-- prettier-ignore -->
  ```yaml
  sources:
    - course: "COS126"
      period: "F2022"
      channel: "#126-grading-notifications"
      assignments: ["Hello", "Loops", "Recursion"]
  ```

  <!-- monitor: ["available", "graded"]
      messages:
        - available: "{available} submissions are available for {assignment}!"
        - graded: "{graded} submissions for {assignment} have been graded!" -->

  In the future, it may be possible to customize what data is tracked and the
  content of the notification message.

- **Deterministic Finite Automaton:**

  - Read from `data/*.json`,
  - Fetch recent data from codePost,
  - Compare to cached data,
  - If there are changes, trigger a Slack notification,
  - Update the cached data.

## References

- `utas_slack_bot.py` is the bot that used to send Slack notifications from
  webhooks.
- This is how to store both the codePost key and the Slack OAuth key securely in
  the repository to use them in the GitHub Actions flow:
  https://docs.github.com/en/actions/security-guides/encrypted-secrets.

## Local development

- Clone repository:
  `git clone https://github.com/PrincetonCS-UCA/slack-notifications-grading.git`
- Install the dependencies in a virtual environment: `pipenv install`
- Run in the venv: `pipenv run python utas_slack_bot.py`
