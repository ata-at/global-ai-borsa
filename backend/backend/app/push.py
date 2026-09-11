import os, json
from pywebpush import webpush, WebPushException

SUBSCRIPTIONS = {}

def save_subscription(sub):
    if sub.get("endpoint"):
        SUBSCRIPTIONS[sub["endpoint"]] = sub

def send_push(title, body, url="/"):
    private = os.getenv("VAPID_PRIVATE_KEY","")
    subject = os.getenv("VAPID_SUBJECT","")
    if not private or not subject:
        return {"sent":0,"skipped":True}
    sent = 0
    dead = []
    for endpoint, sub in list(SUBSCRIPTIONS.items()):
        try:
            webpush(
                subscription_info=sub,
                data=json.dumps({"title":title,"body":body,"url":url}),
                vapid_private_key=private,
                vapid_claims={"sub":subject}
            )
            sent += 1
        except WebPushException as e:
            if getattr(e,"response",None) is not None and e.response.status_code in (404,410):
                dead.append(endpoint)
    for e in dead:
        SUBSCRIPTIONS.pop(e,None)
    return {"sent":sent,"skipped":False}
