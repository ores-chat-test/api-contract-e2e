#![forbid(unsafe_code)]

use axum::{
    extract::Request,
    http::{HeaderMap, Method, StatusCode, header},
    middleware::Next,
    response::{IntoResponse, Response},
};

const RPC_PATH: &str = "/v1/rpc";
const JSON_MEDIA_TYPE: &str = "application/json";

pub async fn enforce_rpc_media_contract(request: Request, next: Next) -> Response {
    if request.uri().path() != RPC_PATH || request.method() != Method::POST {
        return next.run(request).await;
    }

    if !headers_have_single_json_content_type(request.headers()) {
        return StatusCode::UNSUPPORTED_MEDIA_TYPE.into_response();
    }

    if !accept_headers_allow_json(request.headers()) {
        return StatusCode::NOT_ACCEPTABLE.into_response();
    }

    return next.run(request).await;
}

fn headers_have_single_json_content_type(headers: &HeaderMap) -> bool {
    let mut headers = headers.get_all(header::CONTENT_TYPE).iter();
    let Some(value) = headers.next() else {
        return false;
    };
    // Content-Type is a singleton. A proxy and an origin must not disagree
    // about which codec is authoritative when duplicate values are supplied.
    if headers.next().is_some() {
        return false;
    }
    value.to_str().ok().is_some_and(is_json_content_type)
}

fn is_json_content_type(value: &str) -> bool {
    let mut parts = value.split(';');
    if !parts
        .next()
        .is_some_and(|part| part.trim().eq_ignore_ascii_case(JSON_MEDIA_TYPE))
    {
        return false;
    }
    let mut saw_charset = false;
    for part in parts {
        let Some((key, raw_value)) = part.trim().split_once('=') else {
            return false;
        };
        let charset = raw_value.trim();
        if !key.trim().eq_ignore_ascii_case("charset")
            || saw_charset
            || !(charset.eq_ignore_ascii_case("utf-8") || charset.eq_ignore_ascii_case("\"utf-8\""))
        {
            return false;
        }
        saw_charset = true;
    }
    true
}

fn accept_headers_allow_json(headers: &HeaderMap) -> bool {
    let mut ranges = Vec::new();
    let mut saw_accept = false;
    for value in headers.get_all(header::ACCEPT).iter() {
        saw_accept = true;
        let Ok(value) = value.to_str() else {
            return false;
        };
        ranges.extend(value.split(','));
    }
    // A missing Accept means any response is acceptable; a present but
    // malformed/empty Accept never gets that permissive default.
    !saw_accept || accepted_json_quality(ranges.into_iter()).is_some_and(|q| q > 0)
}

#[cfg(test)]
fn accepts_json(value: &str) -> bool {
    accepted_json_quality(value.split(',')).is_some_and(|q| q > 0)
}

// RFC 9110 quality grammar: no signs, exponents or more than three decimals.
fn quality_thousandths(raw: &str) -> Option<u16> {
    let (whole, fraction) = raw.split_once('.').unwrap_or((raw, ""));
    if !matches!(whole, "0" | "1")
        || fraction.len() > 3
        || !fraction.bytes().all(|byte| byte.is_ascii_digit())
    {
        return None;
    }
    if whole == "1" {
        return fraction.bytes().all(|byte| byte == b'0').then_some(1000);
    }
    let digits = fraction.parse::<u16>().unwrap_or(0);
    Some(digits * 10u16.pow((3 - fraction.len()) as u32))
}

// An exact application/json range governs the response even when a broader
// application/* or */* range allows traffic. For duplicate equal-specificity
// ranges, choose the stricter quality so an explicit denial cannot be bypassed.
fn accepted_json_quality<'a>(items: impl Iterator<Item = &'a str>) -> Option<u16> {
    let mut selected: Option<(u8, u16)> = None;
    for item in items {
        let mut pieces = item.split(';');
        let media_type = pieces.next().unwrap_or_default().trim();
        let specificity = if media_type.eq_ignore_ascii_case(JSON_MEDIA_TYPE) {
            3
        } else if media_type.eq_ignore_ascii_case("application/*") {
            2
        } else if media_type == "*/*" {
            1
        } else {
            continue;
        };
        let mut quality = 1000;
        let mut seen_quality = false;
        for parameter in pieces {
            let mut pair = parameter.trim().splitn(2, '=');
            let key = pair.next().unwrap_or_default().trim();
            if key.eq_ignore_ascii_case("q") {
                let raw = pair.next().unwrap_or_default().trim();
                quality = if seen_quality {
                    0
                } else {
                    quality_thousandths(raw).unwrap_or(0)
                };
                seen_quality = true;
            }
        }
        selected = match selected {
            Some((existing_specificity, existing_quality))
                if existing_specificity > specificity =>
            {
                Some((existing_specificity, existing_quality))
            }
            Some((existing_specificity, existing_quality))
                if existing_specificity == specificity =>
            {
                Some((specificity, existing_quality.min(quality)))
            }
            _ => Some((specificity, quality)),
        };
    }
    selected.map(|(_, quality)| quality)
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::HeaderValue;
    use serde_json::Value;

    #[test]
    fn content_type_requires_json_without_sniffing() {
        assert!(is_json_content_type("application/json"));
        assert!(is_json_content_type("application/json; charset=utf-8"));
        assert!(!is_json_content_type("application/msgpack"));
        assert!(!is_json_content_type("application/cbor"));
        assert!(!is_json_content_type("application/x-protobuf"));
        assert!(!is_json_content_type("application/octet-stream"));
        assert!(!is_json_content_type("text/json"));
    }

    #[test]
    fn content_type_duplicate_headers_fail_closed() {
        let mut headers = HeaderMap::new();
        assert!(!headers_have_single_json_content_type(&headers));
        headers.append(
            header::CONTENT_TYPE,
            HeaderValue::from_static("application/json"),
        );
        assert!(headers_have_single_json_content_type(&headers));
        headers.append(
            header::CONTENT_TYPE,
            HeaderValue::from_static("application/msgpack"),
        );
        assert!(!headers_have_single_json_content_type(&headers));
        let mut repeated = HeaderMap::new();
        repeated.append(
            header::CONTENT_TYPE,
            HeaderValue::from_static("application/json"),
        );
        repeated.append(
            header::CONTENT_TYPE,
            HeaderValue::from_static("application/json"),
        );
        assert!(!headers_have_single_json_content_type(&repeated));
    }

    #[test]
    fn content_type_charset_and_parameters_fail_closed() {
        assert!(is_json_content_type("APPLICATION/JSON; CHARSET=UTF-8"));
        assert!(is_json_content_type("application/json; charset=\"utf-8\""));
        for bad in [
            "application/json; charset=utf-16",
            "application/json; charset=ascii",
            "application/json; charset=utf-8; charset=utf-8",
            "application/json; charset=utf-8; unexpected=yes",
            "application/json; malformed",
            "application/json;",
            "application/json, application/msgpack",
            "application/json; charset=\"utf-8",
        ] {
            assert!(!is_json_content_type(bad), "accepted {bad:?}");
        }
    }

    #[test]
    fn accept_honors_wildcards_and_quality() {
        assert!(accepts_json("application/json"));
        assert!(accepts_json("application/*"));
        assert!(accepts_json("*/*"));
        assert!(accepts_json("application/msgpack, application/json;q=0.5"));
        assert!(!accepts_json("application/msgpack"));
        assert!(!accepts_json("application/cbor"));
        assert!(!accepts_json("application/x-protobuf"));
        assert!(!accepts_json("application/octet-stream"));
        assert!(!accepts_json("application/json;q=0"));
        assert!(!accepts_json("application/json;q=garbage"));
    }

    #[test]
    fn multiple_accept_lines_are_combined_semantically() {
        let mut headers = HeaderMap::new();
        headers.append(
            header::ACCEPT,
            HeaderValue::from_static("application/msgpack"),
        );
        headers.append(
            header::ACCEPT,
            HeaderValue::from_static("application/json;q=0.2"),
        );
        assert!(accept_headers_allow_json(&headers));

        let mut rejected = HeaderMap::new();
        rejected.append(
            header::ACCEPT,
            HeaderValue::from_static("application/msgpack"),
        );
        rejected.append(
            header::ACCEPT,
            HeaderValue::from_static("application/json;q=0"),
        );
        assert!(!accept_headers_allow_json(&rejected));
    }

    #[test]
    fn explicit_json_denial_overrides_broader_wildcard_admission() {
        assert!(!accepts_json("application/json;q=0, */*;q=1"));
        assert!(!accepts_json("*/*;q=1, application/json;q=0"));
        assert!(!accepts_json("application/*;q=1, application/json;q=0"));
        assert!(accepts_json("application/json;q=0.5, */*;q=0"));
    }

    #[test]
    fn duplicate_exact_ranges_and_invalid_quality_fail_closed() {
        assert!(!accepts_json("application/json;q=1, application/json;q=0"));
        assert!(!accepts_json("application/json;q=1e0"));
        assert!(!accepts_json("application/json;q=+1"));
        assert!(!accepts_json("application/json;q=0.0001"));
        assert!(!accepts_json("application/json;q=0.5;q=1"));
        assert!(accepts_json("application/json;q=0.001"));
        assert!(accepts_json("application/json;q=1.000"));
    }

    #[test]
    fn explicit_denial_across_multiple_accept_header_lines_is_binding() {
        let mut headers = HeaderMap::new();
        headers.append(header::ACCEPT, HeaderValue::from_static("*/*;q=1"));
        headers.append(
            header::ACCEPT,
            HeaderValue::from_static("application/json;q=0"),
        );
        assert!(!accept_headers_allow_json(&headers));
    }

    #[test]
    fn missing_accept_defaults_to_json_compatibility() {
        assert!(accept_headers_allow_json(&HeaderMap::new()));
    }

    #[test]
    fn generation_plan_requires_full_codec_and_response_metadata_contract() {
        let plan: Value = serde_json::from_str(include_str!("../fixtures/generation-plan.v1.json"))
            .expect("generation plan fixture must parse");
        let wire = &plan["wire"];
        let codecs = wire["structured_codecs"]
            .as_array()
            .expect("structured codecs");
        let names: Vec<&str> = codecs
            .iter()
            .map(|entry| entry["name"].as_str().expect("codec name"))
            .collect();
        assert_eq!(names, vec!["json", "messagepack", "cbor", "protobuf"]);
        assert_eq!(wire["raw_binary_content_type"], "application/octet-stream");
        assert_eq!(wire["unsupported_request_status"], 415);
        assert_eq!(wire["unacceptable_response_status"], 406);
        assert_eq!(wire["no_content_sniffing"], true);
        assert_eq!(wire["no_explicit_codec_fallback"], true);
        assert_eq!(wire["preserve_native_bytes"], true);
        assert_eq!(wire["max_unary_response_bytes"], 2 * 1024 * 1024);
        assert_eq!(wire["streaming_preserves_chunk_boundaries"], true);
        assert_eq!(wire["streaming_requires_backpressure"], true);
        assert_eq!(wire["compression_is_separate_from_serialization"], true);
        assert_eq!(
            wire["response_metadata_required"],
            serde_json::json!(["status", "content-type"])
        );
        assert_eq!(wire["decode_requires_success_status"], true);
        assert_eq!(wire["decode_requires_content_type_match"], true);
        assert_eq!(
            plan["implemented"],
            serde_json::json!(["rust", "typescript", "dart", "wasm"])
        );
        assert_eq!(plan["rules"]["raw_binary_is_not_a_structured_codec"], true);
        assert_eq!(plan["rules"]["protobuf_numbers_are_ledger_owned"], true);
    }
}
