from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from services.market_universe.lifecycle import MarketLifecycleRecord
from services.market_universe.router import MarketUniverseRouter, UniverseCensusResult

CAPTURED_AT = datetime(2026, 8, 25, 20, 0, tzinfo=UTC)


def _series(
    *,
    source_name: str = "Official Source",
    source_url: str = "https://example.invalid",
) -> dict[str, object]:
    return {
        "ticker": "KXSERIES",
        "title": "Test series",
        "category": "Economics",
        "frequency": "daily",
        "settlement_sources": [{"name": source_name, "url": source_url}],
    }


def _event(ticker: str = "KXEVENT", *, series_ticker: str = "KXSERIES") -> dict[str, object]:
    return {
        "event_ticker": ticker,
        "series_ticker": series_ticker,
        "title": "Test event",
        "category": "Economics",
    }


def _market(ticker: str = "KXEVENT-10", **changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "ticker": ticker,
        "event_ticker": "KXEVENT",
        "title": "Test threshold",
        "market_type": "binary",
        "status": "active",
        "rules_primary": "The market resolves Yes if the official value is at least 10.",
        "price_level_structure": "standard",
        "timezone": "UTC",
        "expiration_time": "2026-08-26T20:00:00Z",
        "volume_fp": "12.00",
        "open_interest_fp": "3.00",
    }
    row.update(changes)
    return row


def _quote_fields() -> dict[str, object]:
    return {
        "yes_bid_dollars": "0.40",
        "yes_ask_dollars": "0.45",
        "yes_bid_size_fp": "8.00",
        "yes_ask_size_fp": "9.00",
        "no_bid_dollars": "0.55",
        "no_ask_dollars": "0.60",
        "volume_24h_fp": "7.00",
        "liquidity_dollars": "25.00",
    }


def _census(
    markets: list[dict[str, object]],
    *,
    event_rows: list[dict[str, object]] | None = None,
    series_rows: list[dict[str, object]] | None = None,
    response_sha256: str = "a" * 64,
    previous_records: Mapping[str, MarketLifecycleRecord] | None = None,
) -> UniverseCensusResult:
    return MarketUniverseRouter().census(
        market_rows=markets,
        event_rows=[_event()] if event_rows is None else event_rows,
        series_rows=[_series()] if series_rows is None else series_rows,
        source_authority="captured-public-kalshi",
        request_locator="fixture://ku-a2-identity-freeze",
        response_sha256=response_sha256,
        captured_at=CAPTURED_AT,
        previous_records=previous_records,
    )


def _ids(result: UniverseCensusResult) -> dict[str, object]:
    return {
        "lifecycle_record_ids": [record.lifecycle_record_id for record in result.records],
        "quarantine_ids": [record.quarantine_id for record in result.quarantines],
        "census_manifest_id": result.manifest.manifest_id,
        "descriptor_ids": [record.descriptor_id for record in result.coverage_descriptors],
        "family_coverage_manifest_id": result.coverage_manifest.manifest_id,
    }


EXPECTED_BASELINE: dict[str, object] = {
    "material_supersession_first": {
        "census_manifest_id": "aeccd1babe39eef352c23b9f1b4214d17cae90e3d9348e814396c421ea2ad8f0",
        "descriptor_ids": [
            "33bfba3b1dec765e42db7b904fb76434a015f7c104c3c608318a2bf9ead1c95d",
        ],
        "family_coverage_manifest_id": (
            "55cc4f380878cb4be8b6659cc76c22b19c9f5221260486ac33a2233f566e5a5f"
        ),
        "lifecycle_record_ids": [
            "d7a7a97ea5e9306f1e0a59d2761ca39bcdf2c256171525aed419c5b7d0dc402a",
        ],
        "quarantine_ids": [],
    },
    "material_supersession_second": {
        "census_manifest_id": "aeff15ed76fcf97e04fd7804ec981b9b7a5df77e22223cf14b5c89db9e486338",
        "descriptor_ids": [
            "9305be2e88032d49b4c67349c3bf8ed2da9c4bbd512183d6ab05ff43c8574a28",
        ],
        "family_coverage_manifest_id": (
            "b1d7d9e0e55caccc8d0e8980e6e4da2384500a4bb3d883d0579b27d151cd978e"
        ),
        "lifecycle_record_ids": [
            "4cd721bb5f327af59bcd207100222b8d3e55db7662b8b46497abcd29fdbeee0f",
        ],
        "quarantine_ids": [],
    },
    "missing_parent": {
        "census_manifest_id": "1aed9696f64d5b31e948e933b6afd72777d10e654a96eaa1884aeb932c225b92",
        "descriptor_ids": [
            "6047ab87dd5643fc2bfe8cc441d57f98844fb595491768c8f537fa45b62664e3",
        ],
        "family_coverage_manifest_id": (
            "ab6a0c9f67adc90b60ef76f71c8805e2a6f773ece99a3394ca0fc6ab0c93cd20"
        ),
        "lifecycle_record_ids": [
            "475724154fe11667e305670ec40b3132a99c3ebc35d88ec35d288659be6a2f69",
        ],
        "quarantine_ids": [],
    },
    "mixed": {
        "census_manifest_id": "1dc598a238a0bed370554ba162bd79ae3da92ec8b8c347a2607b392d62e8a758",
        "descriptor_ids": [
            "33bfba3b1dec765e42db7b904fb76434a015f7c104c3c608318a2bf9ead1c95d",
        ],
        "family_coverage_manifest_id": (
            "4991198d576904be0be6dd86de42fcfab06b8a10e40086af0446e54b7500720d"
        ),
        "lifecycle_record_ids": [
            "d7a7a97ea5e9306f1e0a59d2761ca39bcdf2c256171525aed419c5b7d0dc402a",
        ],
        "quarantine_ids": [
            "cdbf314c394a5441ece7a825fcc32ca716bcf884282e66647a4d03777221a919",
        ],
    },
    "mve": {
        "census_manifest_id": "f03630e48098f4ce6b4bbe6ef8d9e370310b096d81f41a2ad8959b30b1994570",
        "descriptor_ids": [
            "33769b59d372a7e2262a8878112bb6c513b16cf4dd41393453ccf9db248a8e3c",
        ],
        "family_coverage_manifest_id": (
            "86273f637973b38a4c334a3258524a25d66277ac7a53e312b83e8f401b68d45b"
        ),
        "lifecycle_record_ids": [
            "cf1cdc5c66e324993f52aeaf57e7c12036cbb3df83ac2fb39758000a249f7325",
        ],
        "quarantine_ids": [],
    },
    "non_event": {
        "census_manifest_id": "67bedd86d41eb35e4aa448b9c2315f6a862d54ac444d39e18334b3753bc9d1c8",
        "descriptor_ids": [
            "ed22e22881b2d71541637ea056e4c2aafce532b47ff51a66ea7fff324466c7c0",
        ],
        "family_coverage_manifest_id": (
            "c303864c4b0a9602b61b85cb8b67b060e7d0b12923494701ee4c17ec01abda29"
        ),
        "lifecycle_record_ids": [
            "b76cdebed96ef0485126c7cd558f270c2feee3ee811c0f8f2bd9193d9ab4b8ae",
        ],
        "quarantine_ids": [],
    },
    "price_only_first": {
        "census_manifest_id": "35499293c16d0362232d29b0b8bae9a57d62555c4f18bf6dd5d7cbda15d676a2",
        "descriptor_ids": [
            "3e92c405b4743482270f309afc340ebeb521aade488765e3ce8865ae21bd4563",
        ],
        "family_coverage_manifest_id": (
            "cdcb7f3347ce5c1690ef4f3a1d005047d2953944e7798a8474b8ee480f9a2fb8"
        ),
        "lifecycle_record_ids": [
            "674e92a887aa01056b2a93e469dbc628950073bb67d77e89aedb8342a60de328",
        ],
        "quarantine_ids": [],
    },
    "price_only_second": {
        "census_manifest_id": "afcc595202e28bb7b71405dc2247db1960671364c9d1b4761b8450432a4f1ef1",
        "descriptor_ids": [
            "360a01756f787af86239dd9b22020b3032b53873e9c5cd87063d3b30797cffe5",
        ],
        "family_coverage_manifest_id": (
            "e145b99016397cbb8916e590c68c7e812e67373431ca1db48079ecc9bb33cc98"
        ),
        "lifecycle_record_ids": [
            "c81ecf8bd0782a96782eef1c186a9d194f71e1f4f1b482b5b8a976922a0f6ff9",
        ],
        "quarantine_ids": [],
    },
    "quarantine": {
        "census_manifest_id": "7e9dc576970e71b5c1582706f91a178398a437fe396d319b1dbfa841cdb6f7e1",
        "descriptor_ids": [],
        "family_coverage_manifest_id": (
            "8c2b050bbcbd6f338f78c72e078003f2b4b58ebfa83c4d506a41b6653bfcb92d"
        ),
        "lifecycle_record_ids": [],
        "quarantine_ids": [
            "1c46cbad6f91c603f92f667b85d6c78ab4bfb4c21f2ca5c36a8076ebdd21da55",
        ],
    },
    "scalar": {
        "census_manifest_id": "624875012aba2b1893f50d908d47c70dbc654be44c587d55d3a035a6f9284701",
        "descriptor_ids": [
            "ebdbf366080bf456373d05651ae20a2a02448ead4ca763270e498e95bfd21b62",
        ],
        "family_coverage_manifest_id": (
            "40ddaf0158df184869e7f27cf5d61d5674f0a32df5fbca8bb88715557159f37b"
        ),
        "lifecycle_record_ids": [
            "eadf2df6d71bca5bb4eac1d0e2466ba09005efafe283c440c901054a3f044e58",
        ],
        "quarantine_ids": [],
    },
    "settlement_sources": {
        "census_manifest_id": "765b0c11a869c69d29e88f8bead5ab4d78e4e854ade28216b71a44d16bbd4b14",
        "descriptor_ids": [
            "82c7db8f9646a9a13c1a4f60ec1989c72665a397c25c71f2b3052242542b3a71",
        ],
        "family_coverage_manifest_id": (
            "0a47840be06e4cf416efbbfd7721c7fdb8e63298329f9db0aece20f8beb5f25b"
        ),
        "lifecycle_record_ids": [
            "bee8e8648fed46804e803d8d4d8ddc5f7f53b62bdb2c270983eadadd5ba2b5cb",
        ],
        "quarantine_ids": [],
    },
    "unchanged_replay": {
        "census_manifest_id": "aeccd1babe39eef352c23b9f1b4214d17cae90e3d9348e814396c421ea2ad8f0",
        "descriptor_ids": [
            "33bfba3b1dec765e42db7b904fb76434a015f7c104c3c608318a2bf9ead1c95d",
        ],
        "family_coverage_manifest_id": (
            "55cc4f380878cb4be8b6659cc76c22b19c9f5221260486ac33a2233f566e5a5f"
        ),
        "lifecycle_record_ids": [
            "d7a7a97ea5e9306f1e0a59d2761ca39bcdf2c256171525aed419c5b7d0dc402a",
        ],
        "quarantine_ids": [],
    },
    "unsupported": {
        "census_manifest_id": "6b6e8826b8c68fcce4bafadb8d464e766a48a63232bc3213a6591e930c3ddf9f",
        "descriptor_ids": [
            "119caccb9a46d7c084b8b78468096468fe82f6c4ab09d321a4f9f7d07f061c1c",
        ],
        "family_coverage_manifest_id": (
            "f6dfecdea5fae0be09d8a3733b9284fe8c8a8af7e7318f4def498469bf8aaddc"
        ),
        "lifecycle_record_ids": [
            "2f4e64c916bf4bc32935ccb798eab024ede67cece6fe9cdbce2e5203fc92f88e",
        ],
        "quarantine_ids": [],
    },
    "valid": {
        "census_manifest_id": "aeccd1babe39eef352c23b9f1b4214d17cae90e3d9348e814396c421ea2ad8f0",
        "descriptor_ids": [
            "33bfba3b1dec765e42db7b904fb76434a015f7c104c3c608318a2bf9ead1c95d",
        ],
        "family_coverage_manifest_id": (
            "55cc4f380878cb4be8b6659cc76c22b19c9f5221260486ac33a2233f566e5a5f"
        ),
        "lifecycle_record_ids": [
            "d7a7a97ea5e9306f1e0a59d2761ca39bcdf2c256171525aed419c5b7d0dc402a",
        ],
        "quarantine_ids": [],
    },
}


def test_capture_canonical_ku_a1_identity_baseline_before_private_router_refactor() -> None:
    baseline: dict[str, object] = {}

    baseline["valid"] = _ids(_census([_market()]))
    baseline["unsupported"] = _ids(
        _census([_market(rules_primary="The official source decides the outcome.")])
    )
    baseline["missing_parent"] = _ids(_census([_market()], event_rows=[]))
    baseline["mve"] = _ids(_census([_market(mve_collection_ticker="MVE-1")]))
    baseline["scalar"] = _ids(_census([_market(settlement_value_dollars="0.50")]))
    baseline["non_event"] = _ids(_census([_market(product_type="perpetual_future")]))

    malformed = _market()
    malformed.pop("ticker")
    baseline["quarantine"] = _ids(_census([malformed]))

    malformed_sibling = _market("KXEVENT-BAD")
    malformed_sibling.pop("rules_primary")
    baseline["mixed"] = _ids(_census([_market(), malformed_sibling]))
    baseline["settlement_sources"] = _ids(
        _census(
            [_market()],
            series_rows=[
                _series(
                    source_name="Named Source",
                    source_url="https://source.invalid/path",
                )
            ],
        )
    )

    first = _census([_market()])
    first_record = first.records[0]
    material = _census(
        [_market(rules_primary="The official value is at least 11.")],
        response_sha256="b" * 64,
        previous_records={first_record.market_ticker: first_record},
    )
    baseline["material_supersession_first"] = _ids(first)
    baseline["material_supersession_second"] = _ids(material)

    unchanged = _census(
        [_market()],
        previous_records={first_record.market_ticker: first_record},
    )
    baseline["unchanged_replay"] = _ids(unchanged)

    quoted = _census([_market(**_quote_fields())])
    quoted_record = quoted.records[0]
    changed_quote = _quote_fields()
    changed_quote["yes_bid_dollars"] = "0.41"
    changed_quote["no_ask_dollars"] = "0.59"
    price_only = _census(
        [_market(**changed_quote)],
        response_sha256="b" * 64,
        previous_records={quoted_record.market_ticker: quoted_record},
    )
    baseline["price_only_first"] = _ids(quoted)
    baseline["price_only_second"] = _ids(price_only)

    assert baseline == EXPECTED_BASELINE
