"""Safe provider diagnostics: retain identifiers and limits, never request bodies."""
import re


def provider_failure(exc):
    kind = type(exc).__name__
    messages = {
        "AuthenticationError": "The provider rejected the server credentials.",
        "RateLimitError": "The provider rate or quota limit was reached.",
        "BadRequestError": "The provider rejected the model or input.",
        "APITimeoutError": "The model request timed out.",
    }
    message = messages.get(kind, "MAST analysis failed; no negative classifications were inferred.")
    diagnostic = {"exception_type": kind}
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and 100 <= status <= 599:
        diagnostic["http_status"] = status
    request_id = getattr(exc, "request_id", None)
    if isinstance(request_id, str) and re.fullmatch(r"req_[A-Za-z0-9_-]{1,100}", request_id):
        diagnostic["request_id"] = request_id
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return message, diagnostic
    body = body.get("error", body)
    if not isinstance(body, dict):
        return message, diagnostic
    code = body.get("code")
    if not isinstance(code, str):
        code = None
    known_codes = {"string_above_max_length", "context_length_exceeded", "invalid_value",
                   "unsupported_value", "unsupported_parameter", "model_not_found",
                   "insufficient_quota", "rate_limit_exceeded", "invalid_api_key"}
    if isinstance(code, str) and code in known_codes:
        diagnostic["code"] = code
    param = body.get("param")
    if isinstance(param, str) and re.fullmatch(
            r"(?:model|temperature|reasoning_effort|max_completion_tokens|store|messages"
            r"(?:\[\d{1,5}\])?(?:\.content(?:\[\d{1,5}\]\.text)?)?)", param):
        diagnostic["parameter"] = param
    if code == "string_above_max_length":
        message = "The provider rejected an oversized text field in the request."
        raw = body.get("message", "")
        sizes = re.search(r"maximum length (\d{1,12}), but got a string with length (\d{1,12})", raw) if isinstance(raw, str) else None
        if sizes:
            limit, actual = map(int, sizes.groups())
            diagnostic.update(max_characters=limit, actual_characters=actual)
            message += f" Limit: {limit:,} characters; submitted: {actual:,}."
        message += " Reduce the per-request size and start a new analysis; the full trace will be split into more chunks."
    elif code == "context_length_exceeded":
        message = "The provider's token count exceeded the model context window. Reduce the per-request budget and start a new analysis."
    elif code == "model_not_found":
        message = "The configured model is unavailable to this API project."
    elif code in {"unsupported_parameter", "unsupported_value"}:
        message = "The provider rejected a request setting for the configured model."
    return message, diagnostic
