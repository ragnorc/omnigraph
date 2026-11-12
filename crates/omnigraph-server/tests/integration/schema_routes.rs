//! Accepted schema reads and refusal of the removed schema-apply route.

use std::fs;

use axum::body::Body;
use axum::http::{Method, Request, StatusCode};
use omnigraph::db::{Omnigraph, ReadTarget};
use omnigraph_server::api::{
    ErrorOutput, HTTP_API_CONTRACT, HTTP_API_CONTRACT_HEADER, SchemaOutput,
};
use omnigraph_server::{AppState, build_app};
use serde_json::json;
use tower::ServiceExt;

use crate::support::*;

#[tokio::test(flavor = "multi_thread")]
async fn schema_apply_route_is_absent_without_graph_effects() {
    let (temp, app) = app_for_loaded_graph().await;
    let graph = graph_path(temp.path());
    let db = Omnigraph::open_read_only(graph.to_str().unwrap())
        .await
        .unwrap();
    let before = db.snapshot_of(ReadTarget::branch("main")).await.unwrap();
    let source = db.schema_source().to_string();
    let response = app
        .oneshot(
            Request::builder()
                .header(HTTP_API_CONTRACT_HEADER, HTTP_API_CONTRACT)
                .method(Method::POST)
                .uri(g("/schema/apply"))
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::to_vec(&json!({
                        "schema_source": additive_schema_with_nickname()
                    }))
                    .unwrap(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    let reopened = Omnigraph::open_read_only(graph.to_str().unwrap())
        .await
        .unwrap();
    assert_eq!(reopened.schema_source().as_str(), source);
    assert_eq!(
        reopened
            .snapshot_of(ReadTarget::branch("main"))
            .await
            .unwrap()
            .graph_manifest_version(),
        before.graph_manifest_version(),
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn schema_route_returns_current_source() {
    let (_temp, app) = app_for_loaded_graph().await;
    let (status, body) = json_response(
        &app,
        Request::builder()
            .header(HTTP_API_CONTRACT_HEADER, HTTP_API_CONTRACT)
            .uri(g("/schema"))
            .method(Method::GET)
            .body(Body::empty())
            .unwrap(),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    let output: SchemaOutput = serde_json::from_value(body).unwrap();
    assert!(output.schema_source.contains("node Person"));
}

#[tokio::test(flavor = "multi_thread")]
async fn schema_route_requires_bearer_token_when_auth_configured() {
    let (_temp, app) = app_for_loaded_graph_with_auth("demo-token").await;

    let (missing_status, missing_body) = json_response(
        &app,
        Request::builder()
            .header(HTTP_API_CONTRACT_HEADER, HTTP_API_CONTRACT)
            .uri(g("/schema"))
            .method(Method::GET)
            .body(Body::empty())
            .unwrap(),
    )
    .await;
    let missing_error: ErrorOutput = serde_json::from_value(missing_body).unwrap();
    assert_eq!(missing_status, StatusCode::UNAUTHORIZED);
    assert_eq!(
        missing_error.code,
        Some(omnigraph_server::api::ErrorCode::Unauthorized)
    );

    let (ok_status, ok_body) = json_response(
        &app,
        Request::builder()
            .header(HTTP_API_CONTRACT_HEADER, HTTP_API_CONTRACT)
            .uri(g("/schema"))
            .method(Method::GET)
            .header("authorization", "Bearer demo-token")
            .body(Body::empty())
            .unwrap(),
    )
    .await;
    assert_eq!(ok_status, StatusCode::OK);
    let output: SchemaOutput = serde_json::from_value(ok_body).unwrap();
    assert!(!output.schema_source.is_empty());
}

#[tokio::test(flavor = "multi_thread")]
async fn schema_route_denied_when_actor_lacks_read_permission() {
    let temp = init_loaded_graph().await;
    let graph = graph_path(temp.path());
    let policy_path = temp.path().join("policy.yaml");
    // Policy grants branch_create only — no read action for act-bruno.
    fs::write(&policy_path, INGEST_CREATE_ONLY_POLICY_YAML).unwrap();
    let state = AppState::open_with_bearer_tokens_and_policy(
        graph.to_string_lossy().to_string(),
        vec![("act-bruno".to_string(), "team-token".to_string())],
        Some(&policy_path),
    )
    .await
    .unwrap();
    let app = build_app(state);

    let (status, body) = json_response(
        &app,
        Request::builder()
            .header(HTTP_API_CONTRACT_HEADER, HTTP_API_CONTRACT)
            .uri(g("/schema"))
            .method(Method::GET)
            .header("authorization", "Bearer team-token")
            .body(Body::empty())
            .unwrap(),
    )
    .await;
    let error: ErrorOutput = serde_json::from_value(body).unwrap();
    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(
        error.code,
        Some(omnigraph_server::api::ErrorCode::Forbidden)
    );
}

/// Issue #650: a client reads one type's structured description (its
/// properties and adjacent edges) and a cache revision without parsing `.pg`
/// source. Either served surface may answer: the schema route, or GQ over the
/// graph's own types as the engine catalogue already does for the binary.
#[tokio::test(flavor = "multi_thread")]
async fn one_type_is_described_without_parsing_source_issue_650() {
    let (_temp, app) = app_for_loaded_graph().await;

    let (status, schema) = json_response(
        &app,
        Request::builder()
            .header(HTTP_API_CONTRACT_HEADER, HTTP_API_CONTRACT)
            .uri(g("/schema"))
            .method(Method::GET)
            .body(Body::empty())
            .unwrap(),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let route_keys: Vec<&String> = schema.as_object().unwrap().keys().collect();
    let (status, schema) = json_response(
        &app,
        Request::builder()
            .header(HTTP_API_CONTRACT_HEADER, HTTP_API_CONTRACT)
            .uri(g("/schema?type=Person"))
            .method(Method::GET)
            .body(Body::empty())
            .unwrap(),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "{schema}");
    let described_by_route =
        schema.get("types").is_some() && schema.get("schema_revision").is_some();

    let (gq_status, gq_body) = json_response(
        &app,
        Request::builder()
            .header(HTTP_API_CONTRACT_HEADER, HTTP_API_CONTRACT)
            .uri(g("/query"))
            .method(Method::POST)
            .header("content-type", "application/json")
            .body(Body::from(
                serde_json::to_vec(&json!({
                    "query": "query person() { match { $p: @Property { owner: \"Person\" } } return { $p.name } }"
                }))
                .unwrap(),
            ))
            .unwrap(),
    )
    .await;
    let described_by_gq = gq_status == StatusCode::OK;

    assert!(
        described_by_route || described_by_gq,
        "no served surface describes Person without its .pg source: \
         GET /schema has only {route_keys:?}; GQ over @Property answered {gq_status}: {gq_body}"
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn schema_route_describes_one_type_and_its_edges() {
    let (_temp, app) = app_for_loaded_graph().await;
    let get = |uri: &str| {
        Request::builder()
            .header(HTTP_API_CONTRACT_HEADER, HTTP_API_CONTRACT)
            .uri(g(uri))
            .method(Method::GET)
            .body(Body::empty())
            .unwrap()
    };
    let (status, body) = json_response(&app, get("/schema?type=Person")).await;
    assert_eq!(status, StatusCode::OK, "{body}");
    let output: SchemaOutput = serde_json::from_value(body).unwrap();
    let revision = output.schema_revision.clone().expect("revision");
    let types = output.types.expect("types");
    let names: Vec<_> = types
        .iter()
        .map(|ty| (ty.kind.as_str(), ty.name.as_str()))
        .collect();
    assert_eq!(
        names,
        [("node", "Person"), ("edge", "Knows"), ("edge", "WorksAt")]
    );
    let person = &types[0];
    assert_eq!(person.key.as_deref(), Some(&["name".to_string()][..]));
    let mut props: Vec<_> = person
        .properties
        .iter()
        .map(|p| (p.name.as_str(), p.type_name.as_str(), p.nullable))
        .collect();
    props.sort();
    assert_eq!(props, [("age", "I32", true), ("name", "String", false)]);
    assert_eq!(types[2].from.as_deref(), Some("Person"));
    assert_eq!(types[2].to.as_deref(), Some("Company"));

    let (status, body) = json_response(&app, get("/schema?type=Knows")).await;
    assert_eq!(status, StatusCode::OK, "{body}");
    let output: SchemaOutput = serde_json::from_value(body).unwrap();
    assert_eq!(output.schema_revision.as_deref(), Some(revision.as_str()));
    assert_eq!(output.types.unwrap().len(), 1);

    let (status, body) = json_response(&app, get("/schema?type=Nobody")).await;
    assert_eq!(status, StatusCode::NOT_FOUND, "{body}");
    let (status, body) = json_response(&app, get("/schema")).await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.get("types").is_none());
    assert_eq!(body["schema_revision"], revision.as_str());
}
