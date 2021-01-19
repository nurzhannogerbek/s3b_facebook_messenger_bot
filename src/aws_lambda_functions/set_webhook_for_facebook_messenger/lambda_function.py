import logging
import os

# Configure the logging tool in the AWS Lambda function.
logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)

# Initialize constants with parameters to configure.
POSTGRESQL_USERNAME = os.environ["POSTGRESQL_USERNAME"]
POSTGRESQL_PASSWORD = os.environ["POSTGRESQL_PASSWORD"]
POSTGRESQL_HOST = os.environ["POSTGRESQL_HOST"]
POSTGRESQL_PORT = int(os.environ["POSTGRESQL_PORT"])
POSTGRESQL_DB_NAME = os.environ["POSTGRESQL_DB_NAME"]
TELEGRAM_API_URL = "https://api.telegram.org"
APPSYNC_CORE_API_URL = os.environ["APPSYNC_CORE_API_URL"]
APPSYNC_CORE_API_KEY = os.environ["APPSYNC_CORE_API_KEY"]
FACEBOOK_MESSENGER_BOT_VERIFY_TOKEN = os.environ["FACEBOOK_MESSENGER_BOT_VERIFY_TOKEN"]

# The connection to the database will be created the first time the AWS Lambda function is called.
# Any subsequent call to the function will use the same database connection until the container stops.
POSTGRESQL_CONNECTION = None


def lambda_handler(event, context):
    """
    :param event: The AWS Lambda function uses this parameter to pass in event data to the handler.
    :param context: The AWS Lambda function uses this parameter to provide runtime information to your handler.
    """
    # Parse all necessary query parameters.
    try:
        query_string_parameters = event["queryStringParameters"]
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Define all necessary variables.
    hub_mode = query_string_parameters.get("hub.mode", None)
    hub_challenge = query_string_parameters.get("hub.challenge", None)
    hub_verify_token = query_string_parameters.get("hub.verify_token", None)

    if hub_mode == "subscribe" and hub_challenge:
        # Check the verify token value.
        if hub_verify_token != FACEBOOK_MESSENGER_BOT_VERIFY_TOKEN:
            return {
                "statusCode": 403,
                "body": "Verification token mismatch. Check your 'VERIFY_TOKEN'."
            }

        # You must echo back the "hub.challenge" value, when the endpoint is registered as a webhook.
        return {
            "statusCode": 200,
            "body": hub_challenge
        }

    # Return the status code 200.
    return {
        "statusCode": 200
    }
