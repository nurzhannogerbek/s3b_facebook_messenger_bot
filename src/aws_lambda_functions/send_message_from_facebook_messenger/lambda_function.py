import logging
import os
from psycopg2 import connect
from psycopg2.extras import RealDictCursor
from functools import wraps
from typing import *
import json
from threading import Thread
from queue import Queue
import requests
import databases

# Configure the logging tool in the AWS Lambda function.
logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)

# Initialize constants with parameters to configure.
POSTGRESQL_USERNAME = os.environ["POSTGRESQL_USERNAME"]
POSTGRESQL_PASSWORD = os.environ["POSTGRESQL_PASSWORD"]
POSTGRESQL_HOST = os.environ["POSTGRESQL_HOST"]
POSTGRESQL_PORT = int(os.environ["POSTGRESQL_PORT"])
POSTGRESQL_DB_NAME = os.environ["POSTGRESQL_DB_NAME"]
APPSYNC_CORE_API_URL = os.environ["APPSYNC_CORE_API_URL"]
APPSYNC_CORE_API_KEY = os.environ["APPSYNC_CORE_API_KEY"]
FACEBOOK_API_URL = "https://graph.facebook.com/v9.0"
FACEBOOK_MESSENGER_BOT_VERIFY_TOKEN = os.environ["FACEBOOK_MESSENGER_BOT_VERIFY_TOKEN"]

# The connection to the database will be created the first time the AWS Lambda function is called.
# Any subsequent call to the function will use the same database connection until the container stops.
POSTGRESQL_CONNECTION = None


def set_webhook_for_facebook_messenger(query_string_parameters: Dict[AnyStr, Any]) -> Dict[AnyStr, Any]:
    # Define all necessary variables.
    hub_mode = query_string_parameters.get("hub.mode", None)
    hub_challenge = query_string_parameters.get("hub.challenge", None)
    hub_verify_token = query_string_parameters.get("hub.verify_token", None)

    # Check the values of query parameters.
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


def run_multithreading_tasks(functions: List[Dict[AnyStr, Union[Callable, Dict[AnyStr, Any]]]]) -> Dict[AnyStr, Any]:
    # Create the empty list to save all parallel threads.
    threads = []

    # Create the queue to store all results of functions.
    queue = Queue()

    # Create the thread for each function.
    for function in functions:
        # Check whether the input arguments have keys in their dictionaries.
        try:
            function_object = function["function_object"]
        except KeyError as error:
            logger.error(error)
            raise Exception(error)
        try:
            function_arguments = function["function_arguments"]
        except KeyError as error:
            logger.error(error)
            raise Exception(error)

        # Add the instance of the queue to the list of function arguments.
        function_arguments["queue"] = queue

        # Create the thread.
        thread = Thread(target=function_object, kwargs=function_arguments)
        threads.append(thread)

    # Start all parallel threads.
    for thread in threads:
        thread.start()

    # Wait until all parallel threads are finished.
    for thread in threads:
        thread.join()

    # Get the results of all threads.
    results = {}
    while not queue.empty():
        results = {**results, **queue.get()}

    # Return the results of all threads.
    return results


def reuse_or_recreate_postgresql_connection() -> connect:
    global POSTGRESQL_CONNECTION
    if not POSTGRESQL_CONNECTION:
        try:
            POSTGRESQL_CONNECTION = databases.create_postgresql_connection(
                POSTGRESQL_USERNAME,
                POSTGRESQL_PASSWORD,
                POSTGRESQL_HOST,
                POSTGRESQL_PORT,
                POSTGRESQL_DB_NAME
            )
        except Exception as error:
            logger.error(error)
            raise Exception("Unable to connect to the PostgreSQL database.")
    return POSTGRESQL_CONNECTION


def postgresql_wrapper(function):
    @wraps(function)
    def wrapper(**kwargs):
        try:
            postgresql_connection = kwargs["postgresql_connection"]
        except KeyError as error:
            logger.error(error)
            raise Exception(error)
        cursor = postgresql_connection.cursor(cursor_factory=RealDictCursor)
        kwargs["cursor"] = cursor
        result = function(**kwargs)
        cursor.close()
        return result

    return wrapper


@postgresql_wrapper
def get_facebook_messenger_bot_token(**kwargs) -> AnyStr:
    # Check if the input dictionary has all the necessary keys.
    try:
        cursor = kwargs["cursor"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        sql_arguments = kwargs["sql_arguments"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)

    # Prepare the SQL query that returns the facebook messenger's chat bot token.
    sql_statement = """
    select
        channels.channel_technical_id as facebook_messenger_bot_token
    from
        facebook_messenger_business_accounts
    left join channels on
        facebook_messenger_business_accounts.channel_id = channels.channel_id
    where
        facebook_messenger_business_accounts.business_account = %(business_account)s
    limit 1;
    """

    # Execute the SQL query dynamically, in a convenient and safe way.
    try:
        cursor.execute(sql_statement, sql_arguments)
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Return facebook messenger's chat bot token.
    return cursor.fetchone()["facebook_messenger_bot_token"]


def send_message_to_facebook_messenger(**kwargs) -> None:
    # Check if the input dictionary has all the necessary keys.
    try:
        facebook_messenger_bot_token = kwargs["facebook_messenger_bot_token"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        recipient_id = kwargs["recipient_id"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        message_text = kwargs["message_text"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)

    # Create the request URL address.
    request_url = "{0}/me/messages".format(FACEBOOK_API_URL)

    # Create the parameters.
    parameters = {
        "access_token": facebook_messenger_bot_token
    }

    # Define the headers.
    headers = {
        "Content-Type": "application/json"
    }

    # Define the JSON object body of the POST request.
    data = {
        "recipient": {
            "id": recipient_id
        },
        "message": {
            "text": message_text
        }
    }

    # Execute the POST request.
    try:
        response = requests.post(request_url, params=parameters, headers=headers, data=json.dumps(data))
        response.raise_for_status()
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Return nothing.
    return None


@postgresql_wrapper
def get_aggregated_data(**kwargs) -> Dict:
    # Check if the input dictionary has all the necessary keys.
    try:
        cursor = kwargs["cursor"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        sql_arguments = kwargs["sql_arguments"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)

    # Prepare the SQL query that returns the aggregated data.
    sql_statement = """
    select
        chat_rooms.chat_room_id,
        chat_rooms.channel_id,
        chat_rooms.chat_room_status,
        users.user_id as client_id
    from
        facebook_messenger_chat_rooms
    left join chat_rooms on
        facebook_messenger_chat_rooms.chat_room_id = chat_rooms.chat_room_id
    left join chat_rooms_users_relationship on
        chat_rooms.chat_room_id = chat_rooms_users_relationship.chat_room_id
    left join users on
        chat_rooms_users_relationship.user_id = users.user_id
    where
        facebook_messenger_chat_rooms.facebook_messenger_chat_id = %(facebook_messenger_chat_id)s
    and
        (
            users.internal_user_id is null and users.identified_user_id is not null
            or
            users.internal_user_id is null and users.unidentified_user_id is not null
        )
    limit 1;
    """

    # Execute the SQL query dynamically, in a convenient and safe way.
    try:
        cursor.execute(sql_statement, sql_arguments)
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Return the aggregated data.
    return cursor.fetchone()


@postgresql_wrapper
def get_identified_user_data(**kwargs) -> AnyStr:
    # Check if the input dictionary has all the necessary keys.
    try:
        cursor = kwargs["cursor"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        sql_arguments = kwargs["sql_arguments"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)

    # Prepare an SQL query that returns the data of the identified user.
    sql_statement = """
    select
        users.user_id::text
    from
        identified_users
    left join users on
        identified_users.identified_user_id = users.identified_user_id
    where
        identified_users.facebook_messenger_psid = %(facebook_messenger_psid)s
    and
        users.internal_user_id is null
    and
        users.unidentified_user_id is null
    limit 1;
    """

    # Execute the SQL query dynamically, in a convenient and safe way.
    try:
        cursor.execute(sql_statement, sql_arguments)
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Return the id of the user.
    result = cursor.fetchone()
    if result is None:
        user_id = None
    else:
        user_id = result["user_id"]
    return user_id


@postgresql_wrapper
def create_identified_user(**kwargs) -> AnyStr:
    # Check if the input dictionary has all the necessary keys.
    try:
        cursor = kwargs["cursor"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        sql_arguments = kwargs["sql_arguments"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        facebook_messenger_psid = sql_arguments["facebook_messenger_psid"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        facebook_messenger_bot_token = kwargs["facebook_messenger_bot_token"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)

    # Create the request URL address.
    request_url = "{0}/{1}".format(FACEBOOK_API_URL, facebook_messenger_psid)

    # Create the parameters.
    parameters = {
        "fields": "id,age_range,birthday,email,favorite_athletes,favorite_teams,first_name,gender,hometown,"
                  "inspirational_people,install_type,installed,is_guest_user,languages,last_name,location,"
                  "meeting_for,middle_name,name,name_format,payment_pricepoints,profile_pic,quotes,short_name,"
                  "significant_other,sports,supports_donate_button_in_live_video,video_upload_limits,accounts,"
                  "ad_studies,albums,apprequests,assigned_business_asset_groups,business_users,conversations,feed,"
                  "friends,groups,likes,live_encoders,live_videos,music,photos,picture,videos",
        "access_token": facebook_messenger_bot_token
    }

    # Execute the GET request.
    # https://developers.facebook.com/docs/graph-api/reference/user/
    try:
        response = requests.get(request_url, params=parameters)
        response.raise_for_status()
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Define a few necessary variables that will be used in the future.
    sql_arguments["metadata"] = json.dumps(response.json())
    sql_arguments["identified_user_first_name"] = response.json().get("first_name", None)
    sql_arguments["identified_user_last_name"] = response.json().get("last_name", None)

    # Prepare the SQL query that creates identified user.
    sql_statement = """
    insert into identified_users(
        identified_user_first_name,
        identified_user_last_name,
        metadata,
        facebook_messenger_psid
    ) values(
        %(identified_user_first_name)s,
        %(identified_user_last_name)s,
        %(metadata)s,
        %(facebook_messenger_psid)s
    )
    on conflict on constraint identified_users_facebook_messenger_psid_key 
    do nothing
    returning
        identified_user_id::text;
    """

    # Execute the SQL query dynamically, in a convenient and safe way.
    try:
        cursor.execute(sql_statement, sql_arguments)
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Define the id of the created identified user.
    sql_arguments["identified_user_id"] = cursor.fetchone()["identified_user_id"]

    # Prepare the SQL query that creates the user.
    sql_statement = "insert into users(identified_user_id) values(%(identified_user_id)s) returning user_id::text;"

    # Execute the SQL query dynamically, in a convenient and safe way.
    try:
        cursor.execute(sql_statement, sql_arguments)
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Return the id of the new created user.
    return cursor.fetchone()["user_id"]


def create_chat_room(**kwargs) -> json:
    # Check if the input dictionary has all the necessary keys.
    try:
        channel_technical_id = kwargs["channel_technical_id"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        client_id = kwargs["client_id"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        last_message_content = kwargs["last_message_content"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        facebook_messenger_chat_id = kwargs["facebook_messenger_chat_id"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)

    # Define the GraphQL mutation.
    query = """
    mutation CreateChatRoom (
        $channelTechnicalId: String!,
        $clientId: String!,
        $lastMessageContent: String!,
        $facebookMessengerChatId: String
    ) {
        createChatRoom(
            input: {
                channelTechnicalId: $channelTechnicalId,
                channelTypeName: "facebook_messenger",
                clientId: $clientId,
                lastMessageContent: $lastMessageContent,
                facebookMessengerChatId: $facebookMessengerChatId
            }
        ) {
            channel {
                channelDescription
                channelId
                channelName
                channelTechnicalId
                channelType {
                    channelTypeDescription
                    channelTypeId
                    channelTypeName
                }
            }
            channelId
            chatRoomId
            chatRoomStatus
            client {
                country {
                    countryAlpha2Code
                    countryAlpha3Code
                    countryCodeTopLevelDomain
                    countryId
                    countryNumericCode
                    countryOfficialName
                    countryShortName
                }
                gender {
                    genderId
                    genderPublicName
                    genderTechnicalName
                }
                metadata
                telegramUsername
                userFirstName
                userId
                userLastName
                userMiddleName
                userPrimaryEmail
                userPrimaryPhoneNumber
                userProfilePhotoUrl
                userSecondaryEmail
                userSecondaryPhoneNumber
                userType
                whatsappProfile
                whatsappUsername
            }
            lastMessageContent
            lastMessageDateTime
            lastMessageFromClientDateTime
            organizationsIds
            unreadMessagesNumber
        }
    }
    """

    # Define the GraphQL variables.
    variables = {
        "channelTechnicalId": channel_technical_id,
        "clientId": client_id,
        "lastMessageContent": last_message_content,
        "facebookMessengerChatId": facebook_messenger_chat_id
    }

    # Define the header setting.
    headers = {
        "x-api-key": APPSYNC_CORE_API_KEY,
        "Content-Type": "application/json"
    }

    # Execute POST request.
    try:
        response = requests.post(
            APPSYNC_CORE_API_URL,
            json={
                "query": query,
                "variables": variables
            },
            headers=headers
        )
        response.raise_for_status()
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Return the JSON object of the response.
    return response.json()


def activate_closed_chat_room(**kwargs):
    # Check if the input dictionary has all the necessary keys.
    try:
        chat_room_id = kwargs["chat_room_id"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        client_id = kwargs["client_id"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        last_message_content = kwargs["last_message_content"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)

    # Define the GraphQL mutation.
    query = """
    mutation ActivateClosedChatRoom (
        $chatRoomId: String!,
        $clientId: String!,
        $lastMessageContent: String!
    ) {
        activateClosedChatRoom(
            input: {
                chatRoomId: $chatRoomId,
                clientId: $clientId,
                lastMessageContent: $lastMessageContent
            }
        ) {
            channel {
                channelDescription
                channelId
                channelName
                channelTechnicalId
                channelType {
                    channelTypeDescription
                    channelTypeId
                    channelTypeName
                }
            }
            channelId
            chatRoomId
            chatRoomStatus
            client {
                country {
                    countryAlpha2Code
                    countryAlpha3Code
                    countryCodeTopLevelDomain
                    countryId
                    countryNumericCode
                    countryOfficialName
                    countryShortName
                }
                gender {
                    genderId
                    genderPublicName
                    genderTechnicalName
                }
                metadata
                telegramUsername
                userFirstName
                userId
                userLastName
                userMiddleName
                userPrimaryEmail
                userPrimaryPhoneNumber
                userProfilePhotoUrl
                userSecondaryEmail
                userSecondaryPhoneNumber
                userType
                whatsappProfile
                whatsappUsername
            }
            lastMessageContent
            lastMessageDateTime
            lastMessageFromClientDateTime
            organizationsIds
            unreadMessagesNumber
        }
    }
    """

    # Define the GraphQL variables.
    variables = {
        "chatRoomId": chat_room_id,
        "clientId": client_id,
        "lastMessageContent": last_message_content
    }

    # Define the header setting.
    headers = {
        "x-api-key": APPSYNC_CORE_API_KEY,
        "Content-Type": "application/json"
    }

    # Execute POST request.
    try:
        response = requests.post(
            APPSYNC_CORE_API_URL,
            json={
                "query": query,
                "variables": variables
            },
            headers=headers
        )
        response.raise_for_status()
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Return nothing.
    return None


def create_chat_room_message(**kwargs):
    # Check if the input dictionary has all the necessary keys.
    try:
        chat_room_id = kwargs["chat_room_id"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        message_author_id = kwargs["message_author_id"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        message_channel_id = kwargs["message_channel_id"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        message_text = kwargs["message_text"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        message_content = kwargs["message_content"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)

    # Define the GraphQL mutation.
    query = """
    mutation CreateChatRoomMessage (
        $chatRoomId: String!,
        $messageAuthorId: String!,
        $messageChannelId: String!,
        $messageText: String,
        $messageContent: String
    ) {
        createChatRoomMessage(
            input: {
                chatRoomId: $chatRoomId,
                localMessageId: null,
                isClient: true,
                messageAuthorId: $messageAuthorId,
                messageChannelId: $messageChannelId,
                messageContent: $messageContent,
                messageText: $messageText,
                quotedMessage: {
                    messageAuthorId: null,
                    messageChannelId: null,
                    messageContent: null,
                    messageId: null,
                    messageText: null
                }
            }
        ) {
            channelId
            channelTypeName
            chatRoomId
            chatRoomStatus
            localMessageId
            messageAuthorId
            messageChannelId
            messageContent
            messageCreatedDateTime
            messageDeletedDateTime
            messageId
            messageIsDelivered
            messageIsRead
            messageIsSent
            messageText
            messageUpdatedDateTime
            quotedMessage {
                messageAuthorId
                messageChannelId
                messageContent
                messageId
                messageText
            }
        }
    }
    """

    # Define the GraphQL variables.
    variables = {
        "chatRoomId": chat_room_id,
        "messageAuthorId": message_author_id,
        "messageChannelId": message_channel_id,
        "messageText": message_text,
        "messageContent": message_content
    }

    # Define the header setting.
    headers = {
        "x-api-key": APPSYNC_CORE_API_KEY,
        "Content-Type": "application/json"
    }

    # Execute POST request.
    try:
        response = requests.post(
            APPSYNC_CORE_API_URL,
            json={
                "query": query,
                "variables": variables
            },
            headers=headers
        )
        response.raise_for_status()
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Return JSON object of the response.
    return response.json()


def update_message_data(**kwargs):
    # Check if the input dictionary has all the necessary keys.
    try:
        chat_room_id = kwargs["chat_room_id"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)
    try:
        messages_ids = kwargs["messages_ids"]
    except KeyError as error:
        logger.error(error)
        raise Exception(error)

    # Define the GraphQL mutation.
    query = """
    mutation UpdateMessageData (
        $chatRoomId: String!,
        $messagesIds: [String!]!
    ) {
        updateMessageData(
            input: {
                chatRoomId: $chatRoomId,
                isClient: true,
                messageStatus: MESSAGE_IS_SENT,
                messagesIds: $messagesIds
            }
        ) {
            chatRoomId
            channelId
            chatRoomMessages {
                messageAuthorId
                messageChannelId
                messageContent
                messageCreatedDateTime
                messageDeletedDateTime
                messageId
                messageIsDelivered
                messageIsRead
                messageIsSent
                messageText
                messageUpdatedDateTime
                quotedMessage {
                    messageAuthorId
                    messageChannelId
                    messageContent
                    messageId
                    messageText
                }
            }
            chatRoomStatus
            unreadMessagesNumber,
            channelTypeName,
            isClient
        }
    }
    """

    # Define the GraphQL variables.
    variables = {
        "chatRoomId": chat_room_id,
        "messagesIds": messages_ids
    }

    # Define the header setting.
    headers = {
        "x-api-key": APPSYNC_CORE_API_KEY,
        "Content-Type": "application/json"
    }

    # Execute POST request.
    try:
        response = requests.post(
            APPSYNC_CORE_API_URL,
            json={
                "query": query,
                "variables": variables
            },
            headers=headers
        )
        response.raise_for_status()
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Return nothing.
    return None


def lambda_handler(event, context):
    """
    :param event: The AWS Lambda function uses this parameter to pass in event data to the handler.
    :param context: The AWS Lambda function uses this parameter to provide runtime information to your handler.
    """
    # Define the http method of the request.
    try:
        http_method = event["requestContext"]["http"]["method"]
    except Exception as error:
        logger.error(error)
        raise Exception(error)

    # Check the http method of the request.
    if http_method == "GET":
        # Define query parameters.
        try:
            query_string_parameters = event["queryStringParameters"]
        except Exception as error:
            logger.error(error)
            raise Exception(error)

        # Set the webhook for facebook messenger.
        response = set_webhook_for_facebook_messenger(query_string_parameters)
    elif http_method == "POST":
        # Define the JSON object body.
        try:
            body = json.loads(event["body"])
        except Exception as error:
            logger.error(error)
            raise Exception(error)

        # Parse the JSON object of the message.
        if body["object"] == "page":
            for entry in body["entry"]:
                for messaging in entry["messaging"]:
                    # Define the value of the message.
                    message = messaging.get("message", None)

                    # Check if it's the "message" and not the "read status" or the "reaction".
                    if message:
                        # Define the value of the message text.
                        message_text = message.get("text", None)

                        # Define the value of the facebook application id.
                        app_id = message.get("app_id", None)

                        # Stop processing the request if the application has sent a message.
                        if app_id is not None:
                            break

                        # Define the sender's id (user id).
                        try:
                            sender_id = messaging["sender"]["id"]
                        except Exception as error:
                            logger.error(error)
                            raise Exception(error)

                        # Define the recipient's id (page id).
                        try:
                            recipient_id = messaging["recipient"]["id"]
                        except Exception as error:
                            logger.error(error)
                            raise Exception(error)

                        # Define the instances of the database connections.
                        postgresql_connection = reuse_or_recreate_postgresql_connection()

                        # Get facebook messenger bot token from the database.
                        facebook_messenger_bot_token = get_facebook_messenger_bot_token(
                            postgresql_connection=postgresql_connection,
                            sql_arguments={
                                "business_account": recipient_id
                            }
                        )

                        # Check if message text is available.
                        if message_text:
                            # Make up the content value of the last chat room message.
                            last_message_content = json.dumps({
                                "messageText": message_text,
                                "messageContent": None
                            })

                            # Get the aggregated data.
                            aggregated_data = get_aggregated_data(
                                postgresql_connection=postgresql_connection,
                                sql_arguments={
                                    "facebook_messenger_chat_id": "{0}:{1}".format(recipient_id, sender_id)
                                }
                            )

                            # Define several variables that will be used in the future.
                            if aggregated_data is not None:
                                chat_room_id = aggregated_data["chat_room_id"]
                                channel_id = aggregated_data["channel_id"]
                                chat_room_status = aggregated_data["chat_room_status"]
                                client_id = aggregated_data["client_id"]
                            else:
                                chat_room_id, channel_id, chat_room_status, client_id = None, None, None, None

                            # Check the chat room status.
                            if chat_room_status is None:
                                # Check whether the user was registered in the system earlier.
                                client_id = get_identified_user_data(
                                    postgresql_connection=postgresql_connection,
                                    sql_arguments={
                                        "facebook_messenger_psid": sender_id
                                    }
                                )

                                # Create the new user.
                                if client_id is None:
                                    client_id = create_identified_user(
                                        postgresql_connection=postgresql_connection,
                                        sql_arguments={
                                            "facebook_messenger_psid": sender_id
                                        },
                                        facebook_messenger_bot_token=facebook_messenger_bot_token
                                    )

                                # Create the new chat room.
                                chat_room = create_chat_room(
                                    channel_technical_id=facebook_messenger_bot_token,
                                    client_id=client_id,
                                    last_message_content=last_message_content,
                                    facebook_messenger_chat_id="{0}:{1}".format(recipient_id, sender_id)
                                )

                                # Define a few necessary variables that will be used in the future.
                                try:
                                    chat_room_id = chat_room["data"]["createChatRoom"]["chatRoomId"]
                                except Exception as error:
                                    logger.error(error)
                                    raise Exception(error)
                                try:
                                    channel_id = chat_room["data"]["createChatRoom"]["channelId"]
                                except Exception as error:
                                    logger.error(error)
                                    raise Exception(error)
                            elif chat_room_status == "completed":
                                # Activate closed chat room before sending a message to the operator.
                                activate_closed_chat_room(
                                    chat_room_id=chat_room_id,
                                    client_id=client_id,
                                    last_message_content=last_message_content
                                )

                            # Send the message to the operator and save it in the database.
                            chat_room_message = create_chat_room_message(
                                chat_room_id=chat_room_id,
                                message_author_id=client_id,
                                message_channel_id=channel_id,
                                message_text=message_text,
                                message_content=None
                            )

                            # Define the id of the created message.
                            try:
                                message_id = chat_room_message["data"]["createChatRoomMessage"]["messageId"]
                            except Exception as error:
                                logger.error(error)
                                raise Exception(error)

                            # Update the data (unread message number / message status) of the created message.
                            update_message_data(
                                chat_room_id=chat_room_id,
                                messages_ids=[message_id]
                            )
                        else:
                            # Define the message text.
                            message_text = "🤖💬\nОбработка данного формата в данный момент невозможна."

                            # Send the prepared text to the client on facebook messenger.
                            send_message_to_facebook_messenger(
                                facebook_messenger_bot_token=facebook_messenger_bot_token,
                                recipient_id=sender_id,
                                message_text=message_text
                            )

        # Return the status code 200.
        response = {
            "statusCode": 200
        }
    else:
        # Return the status code 500.
        response = {
            "statusCode": 500,
            "body": "Unexpected HTTP method."
        }

    # Return response.
    return response
