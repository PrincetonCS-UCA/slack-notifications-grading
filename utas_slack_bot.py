import logging
import os

import codepost
import requests
from flask import Flask, request
from slack import WebClient
from slack.errors import SlackApiError

# A very simple Flask Hello World app for you to get started with...


app = Flask(__name__)

RANDOM_CHANNEL = "CTLAUK751"
MAIN_GRADING_CHANNEL = "CTMLHUD25"
NOTIFICATION_CHANNEL = "C01B1735FCK"


def send_msg(msg, channel=NOTIFICATION_CHANNEL, as_block=False):

    logging.basicConfig(level=logging.DEBUG)

    slack_token = "xoxb-952046195798-1015535820116-UFCiaj1MSIP1qSrE49voaRep"  # os.environ["SLACK_API_TOKEN"]
    client = WebClient(token=slack_token)

    try:
        if as_block:
            response = client.chat_postMessage(
                channel=channel,
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": msg}}],
            )
        else:
            response = client.chat_postMessage(
                channel=channel,
                text=msg,
            )
    except SlackApiError as e:
        return e.response

    return response


@app.route("/")
def hello_world():
    # response = send_msg(channel=RANDOM_CHANNEL, msg="*Bold* _Bold_ `TT`")
    return "Hello from Flask! "  # + str(response)


# {'hook': {'id': 44, 'event': 'submission.isFinalized', 'target': 'http://lumbroso.pythonanywhere.com/hook/submissionFinalized'}, 'data': {'model': 'core.submission', 'pk': 243256, 'fields': {'created': '2020-09-16T00:29:07.134383Z', 'modified': '2020-09-19T00:32:43.925635Z', 'assignment': 6159, 'grader': 564, 'isFinalized': False, 'dateEdited': '2020-09-19T00:32:43.914675Z', 'grade': '4.00', 'queueOrderKey': 0, 'gradeFrozen': False, 'dateUploaded': '2020-09-16T00:29:07.133048Z', 'lateDayCreditsUsed': 0, 'questionIsOpen': False, 'questionIsRegrade': False, 'questionResponder': None, 'questionText': '', 'questionResponse': '', 'questionDate': None, 'responseDate': None, 'testRunsCompleted': 0, 'students': [56648]}, 'updated_fields': ['isFinalized', 'dateEdited']}}


@app.route("/hook/submissionFinalized", methods=["GET", "POST"])
def hook_submission_finalized():
    if request.method == "POST":
        codepost.configure_api_key("b17ee5bcefb4bc6805807b145d2a5f0c8fbe49d3")
        hook_event = dict(request.get_json(force=True))
        submission_id = hook_event["data"]["pk"]
        submission = codepost.submission.retrieve(id=submission_id)
        assignment = codepost.assignment.retrieve(id=submission.assignment)

        if submission.isFinalized:
            all_submissions = assignment.list_submissions()
            finalized_submissions = list(
                filter(lambda s: s.isFinalized, all_submissions)
            )

            completeness = ""
            if len(all_submissions) > 0:
                completeness_num = float(len(finalized_submissions)) / float(
                    len(all_submissions)
                )
                completeness = "   ({:04.2f}% done)".format(completeness_num * 100)

            response = send_msg(
                msg="`{}` has just finalized another submission of *{}*! :tada: {}".format(
                    submission.grader,
                    assignment.name,
                    completeness,
                )
            )

            requests.post(
                "https://webhook.site/cd96e8ef-3649-40a8-b67a-62a0ec394dee",
                data={
                    "response": response,
                    "msg": "`{}` has just finalized another submission of *{}*! :tada: {}".format(
                        submission.grader,
                        assignment.name,
                        completeness,
                    ),
                },
            )
        # response = send_msg(channel=RANDOM_CHANNEL, msg="```\n" + str(dict(request.get_json(force=True))) + "\n```")
        # response = send_msg(channel=RANDOM_CHANNEL, msg="```\n" + submission.grader + "\n```")
        # response = send_msg(channel=RANDOM_CHANNEL, msg="```\n" + str(dict(assignment.__dict__)) + "\n```")
    return "Hello from Flask! "
    return "Hello from Flask!"


# {
#     'hook': {
#         'id': 44,
#         'event': 'submission.isFinalized',
#         'target': 'http://lumbroso.pythonanywhere.com/hook/submissionFinalized'
#     },
#     'data': {
#         'model': 'core.submission',
#         'pk': 243256,
#         'fields': {
#             'created': '2020-09-16T00:29:07.134383Z',
#             'modified': '2020-09-19T00:32:43.925635Z',
#             'assignment': 6159,
#             'grader': 564,
#             'isFinalized': False,
#             'dateEdited': '2020-09-19T00:32:43.914675Z',
#             'grade': '4.00',
#             'queueOrderKey': 0,
#             'gradeFrozen': False,
#             'dateUploaded': '2020-09-16T00:29:07.133048Z',
#             'lateDayCreditsUsed': 0, 'questionIsOpen': False,
#             'questionIsRegrade': False,
#             'questionResponder': None,
#             'questionText': '',
#             'questionResponse': '',
#             'questionDate': None,
#             'responseDate': None,
#             'testRunsCompleted': 0,
#             'students': [56648]
#             },
#         'updated_fields': ['isFinalized', 'dateEdited']
#     }
# }
