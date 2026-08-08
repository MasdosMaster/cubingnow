TRUSTED_RECORD_CLAIM_METHODS = frozenset(
    {
        "api_polling",
        "graphql_subscription",
    }
)

SOURCE_RESULT_TRUST = {
    "api_polling": 100,
    "graphql_subscription": 100,
    "cubingchina_websocket": 20,
}


def record_claim_is_trusted(ingestion_method: str) -> bool:
    return ingestion_method in TRUSTED_RECORD_CLAIM_METHODS


def result_evidence_is_trusted(ingestion_method: str) -> bool:
    return ingestion_method in TRUSTED_RECORD_CLAIM_METHODS


def result_trust_rank(ingestion_method: str) -> int:
    return SOURCE_RESULT_TRUST.get(ingestion_method, 0)
