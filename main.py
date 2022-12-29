from prometheus_client.parser import text_string_to_metric_families
import requests
import boto3
import os
import signal
import time
import json
import logging

logger = logging.getLogger("EventCaller")
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

INIT = 0
II = 1
E = 2
IDLE = 3
S = 4


class ServerStatus:

    def __init__(self, response):
        for metric in text_string_to_metric_families(response):
            if metric.name == "minecraft_status_healthy":
                self.health = metric.samples[0].value == 1
            elif metric.name == "minecraft_status_players_online_count":
                self.is_online = metric.samples[0].value > 0


def main():
    killer = GracefulKiller()
    state = INIT
    while not killer.kill_now:
        try:
            response = requests.get(f"http://{os.environ['HOST']}:8080/metrics")
        except requests.exceptions.ConnectionError:
            logger.warning(f"Could not connect collector")
        else:
            if response.status_code != 200:
                logger.warning(f"Got response {response.status_code} from collector")
            else:
                status = ServerStatus(response.text)
                if state == INIT:
                    if status.health:
                        time_thresh = time.time() + int(os.environ["AUTOSTOP_TIMEOUT_INIT"])
                        ready_event()
                        logger.info(f"MC Server listening for connections - stopping in {os.environ['AUTOSTOP_TIMEOUT_INIT']} seconds")
                        state = II
                elif state == II:
                    if status.is_online:
                        logger.info("Client connected - waiting for disconnect")
                        state = E
                    elif time.time() >= time_thresh:
                        logger.info("No client connected since startup - stopping server")
                        offline_event()
                        state = S
                elif state == E:
                    if not status.is_online:
                        time_thresh = time.time() + int(os.environ["AUTOSTOP_TIMEOUT_EST"])
                        logger.info(f"All clients disconnected - stopping in {os.environ['AUTOSTOP_TIMEOUT_EST']} seconds")
                        state = IDLE
                elif state == IDLE:
                    if status.is_online:
                        logger.info("Client reconnected - waiting for disconnect")
                        state = E
                    elif time.time() >= time_thresh:
                        logger.info("No client reconnected - stopping")
                        offline_event()
                        state = S
                elif state != S:
                    logger.error(f"Error: invalid state {state}")
        time.sleep(int(os.environ["AUTOSTOP_PERIOD"]))


def offline_event():
    stack_response = requests.get("http://169.254.169.254/latest/meta-data/tags/instance/aws:cloudformation:stack-name")
    document_response = requests.get("http://169.254.169.254/latest/dynamic/instance-identity/document")
    if stack_response.status_code == 200 and document_response.status_code == 200:
        stack_name = stack_response.text
        document = document_response.json()
        events = boto3.client('events', document["region"])
        response = events.put_events(
            Entries=[
                {
                    'DetailType': 'Server inactive',
                    'Source': 'oros.mcs',
                    'Resources': [
                        f'arn:aws:ec2:{document["region"]}:{document["accountId"]}:instance/{document["instanceId"]}'
                    ],
                    'Detail': json.dumps({
                        'stack': stack_name,
                        'instance-id': document["instanceId"],
                    })
                }
            ]
        )
        logger.info(response)
    else:
        logger.error(stack_response.text)
        logger.error(document_response.text)


def ready_event():
    stack_response = requests.get("http://169.254.169.254/latest/meta-data/tags/instance/aws:cloudformation:stack-name")
    document_response = requests.get("http://169.254.169.254/latest/dynamic/instance-identity/document")
    if stack_response.status_code == 200 and document_response.status_code == 200:
        stack_name = stack_response.text
        document = document_response.json()
        events = boto3.client('events', document["region"])
        response = events.put_events(
            Entries=[
                {
                    'DetailType': 'Server ready',
                    'Source': 'oros.mcs',
                    'Resources': [
                        f'arn:aws:ec2:{document["region"]}:{document["accountId"]}:instance/{document["instanceId"]}'
                    ],
                    'Detail': json.dumps({
                        'stack': stack_name,
                        'instance-id': document["instanceId"],
                    })
                }
            ]
        )
        logger.info(response)
    else:
        logger.error(stack_response.text)
        logger.error(document_response.text)


class GracefulKiller:
    kill_now = False

    def __init__(self):
        signal.signal(signal.SIGINT, self.exit_gracefully)
        signal.signal(signal.SIGTERM, self.exit_gracefully)

    def exit_gracefully(self, *_):
        self.kill_now = True


if __name__ == '__main__':
    main()
