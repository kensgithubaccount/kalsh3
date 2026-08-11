"""PostgreSQL transaction contract for M13 reservations and M14 journal ownership."""

from dataclasses import dataclass
from typing import Protocol


class Cursor(Protocol):
    def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> object: ...

    def fetchone(self) -> tuple[object, ...] | None: ...


@dataclass(frozen=True, slots=True)
class PostgresExecutionTransaction:
    """Statements execute in one caller-managed SERIALIZABLE transaction."""

    cursor: Cursor

    def acquire(self, authorization_id: str, client_order_id: str, execution_id: str) -> bool:
        self.cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        self.cursor.execute(
            "SELECT state, expires_at FROM risk_authorizations WHERE authorization_id=%s FOR UPDATE",
            (authorization_id,),
        )
        authorization = self.cursor.fetchone()
        if authorization is None or authorization[0] != "ISSUED":
            return False
        self.cursor.execute("SELECT singleton FROM risk_capacity_lock WHERE singleton=1 FOR UPDATE")
        self.cursor.execute(
            "INSERT INTO execution_submission_claims(authorization_id,client_order_id,execution_id) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING RETURNING execution_id",
            (authorization_id, client_order_id, execution_id),
        )
        claimed = self.cursor.fetchone()
        if claimed is None:
            return False
        self.cursor.execute(
            "UPDATE risk_authorizations SET state='CONSUMED' WHERE authorization_id=%s AND state='ISSUED' RETURNING authorization_id",
            (authorization_id,),
        )
        return self.cursor.fetchone() is not None


POSTGRES_RESERVATION_SQL = """
-- Run at SERIALIZABLE isolation. The singleton lock serializes global capacity arithmetic;
-- unique indexes serialize client/authorization identity.
SELECT singleton FROM risk_capacity_lock WHERE singleton = 1 FOR UPDATE;
SELECT authorization_id FROM risk_authorizations
 WHERE authorization_id = %(authorization_id)s AND state = 'ISSUED' FOR UPDATE;
SELECT COALESCE(SUM(market_risk),0), COALESCE(SUM(event_risk),0),
       COALESCE(SUM(aggregate_risk),0)
  FROM risk_reservations WHERE active;
INSERT INTO risk_reservations(authorization_id, market_ticker, event_id,
 market_risk, event_risk, aggregate_risk, cash_commitment, expires_at, active)
VALUES (%(authorization_id)s, %(market)s, %(event)s, %(market_risk)s,
 %(event_risk)s, %(aggregate_risk)s, %(cash)s, %(expires_at)s, true);
"""
