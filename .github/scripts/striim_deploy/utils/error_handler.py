"""Centralized error handling utilities for Striim operations"""

import json
import re
import logging
from typing import List, Dict, Any, Union
import requests


def format_error_message(error_message: str) -> List[str]:
    """
    Format error message for better readability.

    Args:
        error_message: Raw error message

    Returns:
        List of formatted error lines
    """
    # Remove excessive newlines and whitespace
    error_message = error_message.replace("\n\n", "\n").strip()

    # Extract relevant information
    formatted_lines = []

    # Try to parse JSON error message if available
    if error_message.startswith("{") and "}" in error_message:
        try:  # Extract JSON portion if embedded in a larger message
            json_match = re.search(r"({.*})", error_message)
            if json_match:
                json_str = json_match.group(1)
                error_data = json.loads(json_str)

                if "message" in error_data:
                    formatted_lines.append(f"Error: {error_data['message']}")
                elif "componentName" in error_data and "message" in error_data:
                    formatted_lines.append(
                        f"Component {error_data['componentName']}: {error_data['message']}"
                    )

                # Extract relevant information from the message if it exists
                if (
                    "message" in error_data
                    and "Suggested Actions" in error_data["message"]
                ):
                    message_parts = error_data["message"].split("Suggested Actions:")
                    if len(message_parts) > 1:
                        suggested_actions = message_parts[1].strip()
                        formatted_lines.append(
                            f"Suggested Actions: {suggested_actions}"
                        )

                return formatted_lines
        except (json.JSONDecodeError, ValueError):
            pass

    # Simple line-by-line formatting for non-JSON errors
    lines = error_message.split("\n")
    for line in lines:
        if line.strip():
            if "Exception" in line or "Error" in line:
                formatted_lines.append(f"⚠️ {line.strip()}")
            elif "Suggested Actions" in line:
                formatted_lines.append(f"💡 {line.strip()}")
            else:
                formatted_lines.append(line.strip())

    return formatted_lines


def parse_api_error(
    response: Union[Dict[str, Any], requests.Response],
) -> Dict[str, Any]:
    """Parse error information from API response

    Args:
        response: API response (either Response object or dictionary)

    Returns:
        Formatted error dictionary with consistent structure
    """
    # Handle Response objects
    if hasattr(response, "status_code") and not isinstance(response, dict):
        error = {"status_code": response.status_code, "error": True}

        try:
            # Try to parse JSON from response
            error_data = response.json()

            # Process JSON response
            if isinstance(error_data, list) and len(error_data) > 0:
                # Handle case where response is a list of command results
                command_errors = []
                for item in error_data:
                    if item.get("executionStatus") == "Failure":
                        # Get a clean version of the command for display
                        full_command = item.get("command", "")

                        # Get a clean version of the error message
                        error_message = item.get("failureMessage", "")

                        command_errors.append(
                            {
                                "command": full_command,
                                "failure_message": error_message,
                                "response_code": item.get("responseCode", 0),
                            }
                        )
                if command_errors:
                    error["command_errors"] = command_errors
            else:
                # Handle regular error response
                error.update(
                    {
                        "execution_status": error_data.get("executionStatus"),
                        "failure_message": error_data.get("failureMessage"),
                    }
                )
        except (ValueError, AttributeError):
            error["raw_text"] = response.text

        return error

    # Handle dictionary responses that have already been parsed
    elif isinstance(response, dict):
        error = {"status_code": response.get("status_code", 500), "error": True}

        try:
            # Check for command_errors list first
            command_errors = response.get("command_errors", [])
            if command_errors:
                error["command_errors"] = command_errors
                # Extract the first error message for simplified access
                if command_errors and "failure_message" in command_errors[0]:
                    error["message"] = command_errors[0]["failure_message"]
                return error

            # Handle regular error response fields
            failure_message = response.get("failure_message") or response.get(
                "failureMessage"
            )
            if failure_message:
                error["message"] = failure_message

            execution_status = response.get("execution_status") or response.get(
                "executionStatus"
            )
            if execution_status:
                error["status"] = execution_status

            # Include raw text if available
            if "raw_text" in response:
                error["raw_text"] = response.get("raw_text")

        except (KeyError, TypeError):
            # Fallback for unexpected error structure
            error["message"] = "Failed to parse error response"

        return error

    # Handle unexpected input types
    else:
        return {
            "status_code": 500,
            "error": True,
            "message": f"Unsupported response type: {type(response)}",
        }


def format_command_errors(
    command_errors: List[Dict[str, Any]], logger: logging.Logger
) -> None:
    """
    Format and log command errors in a readable way.

    Args:
        command_errors: List of command error dictionaries
        logger: Logger instance
    """
    if not command_errors:
        return

    logger.error("-" * 60)
    logger.error("DEPLOYMENT ERROR DETAILS:")

    for i, err in enumerate(command_errors):
        cmd = err.get("command", "")
        first_statement = cmd.split(";")[0] if ";" in cmd else cmd
        logger.error("• Command %d: %s", i + 1, first_statement)

        # Format and clean up error message
        error_msg = err.get("failure_message", "")
        error_msg = re.sub(r"<[^>]+>", "", error_msg)

        if error_msg:
            formatted_lines = format_error_message(error_msg)
            logger.error("• Error message:")
            for line in formatted_lines:
                logger.error("  %s", line)

        logger.error("• Response Code: %s", err.get("response_code", ""))
        logger.error("")

    logger.error("-" * 60)


def log_error_response(
    response: Union[Dict[str, Any], str, None], logger: logging.Logger
) -> None:
    """
    Log API error response in a consistent format.

    Args:
        response: API response object
        logger: Logger instance
    """
    if isinstance(response, dict):
        command_errors = response.get("command_errors", [])
        if command_errors:
            format_command_errors(command_errors, logger)
            return

        # Handle failure messages as a list or string
        failure_msg = response.get("failure_message") or response.get("failureMessage")
        exec_status = response.get("execution_status") or response.get(
            "executionStatus"
        )

        if exec_status or failure_msg:
            logger.error("-" * 60)
            logger.error("DEPLOYMENT ERROR DETAILS:")

            if exec_status:
                logger.error("• Execution status: %s", exec_status)

            if failure_msg and not isinstance(failure_msg, list):
                failure_msg = re.sub(r"<[^>]+>", "", str(failure_msg))
                logger.error("• Error message:")
                formatted_lines = format_error_message(failure_msg)
                for line in formatted_lines:
                    logger.error("  %s", line)

            logger.error("-" * 60)
            return

        # If we have raw_text, display it as a last resort
        raw_text = response.get("raw_text")
        if raw_text:
            logger.error("-" * 60)
            logger.error("RAW RESPONSE:")
            logger.error("  %s...", raw_text[:300])
            logger.error("-" * 60)
            return

    # Handle string responses
    if isinstance(response, str):
        logger.error("-" * 60)
        logger.error("RAW RESPONSE:")
        # Split the response into chunks to avoid truncation
        max_chunk_size = 1000
        for i in range(0, len(response), max_chunk_size):
            chunk = response[i : i + max_chunk_size]
            logger.error("  %s", chunk)
        logger.error("-" * 60)
        return

    # Handle unexpected response types
    logger.error("Unexpected error response format: %s", type(response))
