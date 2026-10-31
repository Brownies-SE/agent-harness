class SecurityError(Exception):
    """A fixed reason code, safe to expose without echoing untrusted input."""


REASON_CODES = frozenset(
    {
        "",
        "operation_failed",
        "run_failed",
        "run_cancelled",
        "run_timeout",
        "step_limit",
        "approval_denied",
        "audit_unavailable",
        "history_unavailable",
        "capability_denied",
        "invalid_arguments",
        "invalid_json",
        "invalid_request",
        "payload_too_large",
        "tool_failed",
        "unknown_tool",
        "file_required",
        "file_too_large",
        "invalid_path",
        "path_outside_roots",
        "path_traversal",
        "path_unavailable",
        "sensitive_path",
        "symlink_denied",
        "text_file_required",
        "unsafe_file",
        "duplicate_mcp_tool",
        "invalid_mcp_discovery",
        "mcp_already_connected",
        "invalid_model_response",
        "invalid_prompt",
        "http_status_denied",
        "invalid_url",
        "local_only_network_denied",
        "network_address_denied",
        "network_destination_denied",
        "network_failed",
        "network_resolution_failed",
        "response_too_large",
        "network_timeout",
        "resource_denied",
        "cleanup_failed",
    }
)


def safe_reason(reason):
    return reason if isinstance(reason, str) and reason in REASON_CODES else "operation_failed"
