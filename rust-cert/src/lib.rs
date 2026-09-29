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

    if !request
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .is_some_and(is_json_content_type)
    {
        return StatusCode::UNSUPPORTED_MEDIA_TYPE.into_response();
    }

    if !accept_headers_allow_json(request.headers()) {
        return StatusCode::NOT_ACCEPTABLE.into_response();
    }

    return next.run(request).await;
}

fn is_json_content_type(value: &str) -> bool {
    return value
        .split(';')
        .next()
        .is_some_and(|media_type| media_type.trim().eq_ignore_ascii_case(JSON_MEDIA_TYPE));
}

fn accept_headers_allow_json(headers: &HeaderMap) -> bool {
    let values = headers.get_all(header::ACCEPT);
    let mut saw_accept = false;
    for value in values.iter() {
        saw_accept = true;
        let Ok(value) = value.to_str() else {
            return false;
        };
        if accepts_json(value) {
            return true;
        }
    }
    return !saw_accept;
}

fn accepts_json(value: &str) -> bool {
    for item in value.split(',') {
        let mut pieces = item.split(';');
        let media_type = pieces.next().unwrap_or_default().trim().to_ascii_lowercase();
        if media_type.is_empty() {
            continue;
        }

        let mut quality = 1.0_f32;
        for parameter in pieces {
            let mut pair = parameter.trim().splitn(2, '=');
            let name = pair.next().unwrap_or_default().trim();
            let raw_value = pair.next().unwrap_or_default().trim();
            if name.eq_ignore_ascii_case("q") {
                quality = match raw_value.parse::<f32>() {
                    Ok(parsed) if (0.0..=1.0).contains(&parsed) => parsed,
                    _ => 0.0,
                };
            }
        }

        if quality <= 0.0 {
            continue;
        }

        if matches!(media_type.as_str(), JSON_MEDIA_TYPE | "application/*" | "*/*") {
            return true;
        }
    }

    return false;
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
        headers.append(header::ACCEPT, HeaderValue::from_static("application/msgpack"));
        headers.append(header::ACCEPT, HeaderValue::from_static("application/json;q=0.2"));
        assert!(accept_headers_allow_json(&headers));

        let mut rejected = HeaderMap::new();
        rejected.append(header::ACCEPT, HeaderValue::from_static("application/msgpack"));
        rejected.append(header::ACCEPT, HeaderValue::from_static("application/json;q=0"));
        assert!(!accept_headers_allow_json(&rejected));
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
        let codecs = wire["structured_codecs"].as_array().expect("structured codecs");
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
        assert_eq!(plan["implemented"], serde_json::json!(["rust", "typescript", "dart", "wasm"]));
        assert_eq!(plan["rules"]["raw_binary_is_not_a_structured_codec"], true);
        assert_eq!(plan["rules"]["protobuf_numbers_are_ledger_owned"], true);
    }
}
