"""
Skill knowledge as data: which diagnostic tools are relevant to which
root-cause category, and how to interpret each tool's result as evidence
for/against categories. This is the machine-readable counterpart of
docs/skills/*/SKILL.md -- a reasoning aid the agent draws on, not a
decision tree. Tool *selection* is still done dynamically in reasoning.py
based on current hypothesis state, not by iterating this list in order.
"""

# category -> ranked list of diagnostic tools typically most discriminating
# for that category (a priority hint, not a mandatory sequence).
SKILL_TOOL_PRIORITY = {
    "auth_failure": ["inspect_credentials", "get_recent_changes", "test_connectivity"],
    "cert_expiry": ["inspect_certificate_status", "get_recent_changes"],
    "connectivity_failure": ["test_connectivity", "get_recent_changes"],
    "timeout": ["test_connectivity", "compare_with_previous_successful_run", "get_recent_changes"],
    "mapping_error": ["compare_with_previous_successful_run", "get_recent_changes"],
    "malformed_payload": ["compare_with_previous_successful_run", "search_logs"],
    "endpoint_unavailable": ["test_connectivity", "get_recent_changes"],
    "sftp_credential_failure": ["inspect_credentials", "get_recent_changes"],
    "message_processing_failure": ["search_logs", "compare_with_previous_successful_run"],
    "duplicate_message": ["search_logs", "compare_with_previous_successful_run"],
    "database_connection_failure": ["test_connectivity", "inspect_credentials", "get_recent_changes"],
}

ALL_CATEGORIES = list(SKILL_TOOL_PRIORITY.keys())

SUPPLEMENTARY_TOOLS = ["search_historical_incidents", "retrieve_runbook"]

ERROR_CODE_TO_CATEGORY = {
    "401": "auth_failure", "403": "auth_failure", "AUTH_REJECTED": "auth_failure", "TOKEN_EXPIRED": "auth_failure",
    "SSL_HANDSHAKE_FAILED": "cert_expiry", "CERT_EXPIRED": "cert_expiry", "CERT_UNTRUSTED": "cert_expiry",
    "CONN_REFUSED": "connectivity_failure", "HOST_UNREACHABLE": "connectivity_failure",
    "DNS_RESOLUTION_FAILED": "connectivity_failure",
    "READ_TIMEOUT": "timeout", "CONNECT_TIMEOUT": "timeout", "GATEWAY_TIMEOUT_504": "timeout",
    "MAPPING_EXCEPTION": "mapping_error", "SCHEMA_VALIDATION_FAILED": "mapping_error", "XSLT_ERROR": "mapping_error",
    "INVALID_XML": "malformed_payload", "MISSING_MANDATORY_FIELD": "malformed_payload",
    "PAYLOAD_TOO_LARGE": "malformed_payload",
    "SERVICE_UNAVAILABLE_503": "endpoint_unavailable", "ENDPOINT_NOT_FOUND_404": "endpoint_unavailable",
    "SFTP_AUTH_FAILED": "sftp_credential_failure", "SFTP_KEY_REJECTED": "sftp_credential_failure",
    "MESSAGE_STUCK": "message_processing_failure", "PROCESSING_TIMEOUT": "message_processing_failure",
    "QUEUE_BLOCKED": "message_processing_failure",
    "DUPLICATE_MESSAGE_ID": "duplicate_message", "IDEMPOTENCY_CONFLICT": "duplicate_message",
    "DB_CONN_REFUSED": "database_connection_failure", "POOL_EXHAUSTED": "database_connection_failure",
    "ORA-12170": "database_connection_failure", "LOGIN_TIMEOUT": "database_connection_failure",
}

CATEGORY_TO_SKILL = {
    "auth_failure": "Authentication Failure Investigation",
    "cert_expiry": "Certificate Failure Investigation",
    "connectivity_failure": "Connectivity Failure Investigation",
    "timeout": "Timeout/Performance Investigation",
    "mapping_error": "Mapping/Data Transformation Failure Investigation",
    "malformed_payload": "Mapping/Data Transformation Failure Investigation",
    "endpoint_unavailable": "Connectivity Failure Investigation",
    "sftp_credential_failure": "Authentication Failure Investigation",
    "message_processing_failure": "Message Processing Failure Investigation",
    "duplicate_message": "Message Processing Failure Investigation",
    "database_connection_failure": "Connectivity Failure Investigation",
}

CATEGORY_STATEMENTS = {
    "auth_failure": "Interface is failing due to an authentication/credential problem.",
    "cert_expiry": "Interface is failing due to an expired or invalid certificate.",
    "connectivity_failure": "Interface is failing due to a network/connectivity problem.",
    "timeout": "Interface is failing due to elevated latency or an overly strict timeout.",
    "mapping_error": "Interface is failing due to a mapping/transformation defect.",
    "malformed_payload": "Interface is failing due to a malformed source payload.",
    "endpoint_unavailable": "Interface is failing because the target endpoint is unavailable.",
    "sftp_credential_failure": "Interface is failing due to an SFTP credential/key problem.",
    "message_processing_failure": "Messages are stuck or backlogged in processing.",
    "duplicate_message": "The interface has produced a duplicate message.",
    "database_connection_failure": "Interface is failing due to a database connectivity problem.",
}
