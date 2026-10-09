"""PostgreSQL transfer integration tests. Requires an isolated *_test database URL."""
import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

import pytest

pytest.importorskip("jwt")
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.auth import current_user
from app.main import app
from app.models import Account, ApplicationUser, AuditLog, Customer, KYCRecord, LedgerEntry, LedgerTransaction, Role

@pytest.fixture
def world():
    url=os.getenv("BANKING_TEST_DATABASE_URL")
    if not url: pytest.skip("Set BANKING_TEST_DATABASE_URL to an isolated PostgreSQL *_test database")
    engine=create_engine(url,pool_pre_ping=True)
    if not engine.url.database or not (engine.url.database.endswith("_test") or engine.url.database.endswith("_testing")):
        pytest.fail("Refusing to modify a database whose name does not end in _test or _testing")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    Factory=sessionmaker(engine,expire_on_commit=False)
    with Factory.begin() as db:
        admin=Role(name="administrator"); customer_role=Role(name="customer")
        manager=ApplicationUser(supabase_sub=str(uuid4()),email="manager@example.test",roles=[admin])
        alice=ApplicationUser(supabase_sub=str(uuid4()),email="alice@example.test",roles=[customer_role])
        other=ApplicationUser(supabase_sub=str(uuid4()),email="bob@example.test",roles=[customer_role])
        charlie=ApplicationUser(supabase_sub=str(uuid4()),email="charlie@example.test",roles=[customer_role])
        dave=ApplicationUser(supabase_sub=str(uuid4()),email="dave@example.test",roles=[customer_role])
        db.add_all([manager,alice,other,charlie,dave]); db.flush()
        ca=Customer(user_id=alice.id,full_name="Alice Demo",email=alice.email,status="active")
        cb=Customer(user_id=other.id,full_name="Bob Demo",email=other.email,status="active")
        db.add_all([ca,cb]); db.flush()
        db.add_all([KYCRecord(customer_id=ca.id,document_type="synthetic-demo",synthetic_reference="DEMO-ALICE-APPROVED",status="approved"),KYCRecord(customer_id=cb.id,document_type="synthetic-demo",synthetic_reference="DEMO-BOB-APPROVED",status="approved")])
        src=Account(customer_id=ca.id,account_number="TESTALICE0001",account_type="checking",currency="USD",status="active",balance=Decimal("100.00"))
        dst=Account(customer_id=cb.id,account_number="TESTBOB00001",account_type="checking",currency="USD",status="active",balance=Decimal("20.00"))
        eur=Account(customer_id=cb.id,account_number="TESTBOBEUR01",account_type="checking",currency="EUR",status="active",balance=Decimal("20.00"))
        db.add_all([src,dst,eur]); db.flush()
        orphan=Customer(user_id=None,full_name="Charlie Demo",email=charlie.email,status="active")
        db.add(orphan); db.flush()
        db.add(KYCRecord(customer_id=orphan.id,document_type="synthetic-demo",synthetic_reference="DEMO-CHARLIE-APPROVED",status="approved"))
        orphan_account=Account(customer_id=orphan.id,account_number="TESTCHARLIE01",account_type="checking",currency="USD",status="active",balance=Decimal("40.00"))
        clearing_customer=Customer(user_id=None,full_name="Test Clearing",email="clearing@example.test",status="active")
        db.add_all([orphan_account,clearing_customer]); db.flush()
        clearing_usd=Account(customer_id=clearing_customer.id,account_number="TESTCLEARUSD1",account_type="system_clearing",currency="USD",status="active",balance=Decimal("160.00"))
        clearing_eur=Account(customer_id=clearing_customer.id,account_number="TESTCLEAREUR1",account_type="system_clearing",currency="EUR",status="active",balance=Decimal("20.00"))
        db.add_all([clearing_usd,clearing_eur]); db.flush()
        for funded,clearing,amount,currency in [(src,clearing_usd,Decimal("100.00"),"USD"),(dst,clearing_usd,Decimal("20.00"),"USD"),(orphan_account,clearing_usd,Decimal("40.00"),"USD"),(eur,clearing_eur,Decimal("20.00"),"EUR")]:
            opening=LedgerTransaction(description=f"opening-{funded.account_number}",status="posted")
            db.add(opening); db.flush()
            db.add_all([LedgerEntry(transaction_id=opening.id,account_id=funded.id,amount=amount,currency=currency,direction="credit"),LedgerEntry(transaction_id=opening.id,account_id=clearing.id,amount=amount,currency=currency,direction="debit")])
        db.flush()
        ids={"manager":manager.id,"alice":alice.id,"charlie":charlie.id,"dave":dave.id,"src":src.id,"dst":dst.id,"eur":eur.id,"orphan_customer":orphan.id,"orphan_account":orphan_account.id}
    def get_test_db():
        db=Factory()
        try: yield db
        finally: db.close()
    app.dependency_overrides[get_db]=get_test_db
    def client_for(user_id):
        with Factory() as db: user=db.get(ApplicationUser,user_id)
        app.dependency_overrides[current_user]=lambda:user
        return TestClient(app)
    yield {"engine":engine,"factory":Factory,"ids":ids,"client":client_for}
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()

def payload(ids,source=None,destination=None,amount="25.00",currency="USD"):
    return {"source_account_id":str(source or ids["src"]),"destination_account_id":str(destination or ids["dst"]),"amount":amount,"currency":currency,"reference":"test transfer"}

def test_manager_and_customer_transfer_to_other_customer(world):
    manager=world["client"](world["ids"]["manager"])
    mr=manager.post("/api/transfers",json=payload(world["ids"]),headers={"Idempotency-Key":"manager-transfer-001"})
    assert mr.status_code==201,mr.text
    alice=world["client"](world["ids"]["alice"])
    cr=alice.post("/api/transfers",json=payload(world["ids"],amount="10.00"),headers={"Idempotency-Key":"customer-transfer-001"})
    assert cr.status_code==201,cr.text

def test_customer_cannot_debit_another_customers_account(world):
    client=world["client"](world["ids"]["alice"])
    response=client.post("/api/transfers",json=payload(world["ids"],source=world["ids"]["dst"],destination=world["ids"]["src"]),headers={"Idempotency-Key":"ownership-check-001"})
    assert response.status_code==403

def test_customer_sees_only_owned_eligible_source_accounts(world):
    client=world["client"](world["ids"]["alice"])
    identity=client.get("/api/me").json()
    assert identity["id"]==str(world["ids"]["alice"])
    assert identity["email"]=="alice@example.test"
    assert identity["customer_profile"]["kyc_status"]=="approved"
    response=client.get("/api/accounts")
    assert response.status_code==200
    assert {row["id"] for row in response.json()}=={str(world["ids"]["src"])}
    assert response.json()[0]["available_balance"]=="100.00"
    assert response.json()[0]["eligible_for_transfer_source"] is True
    assert client.get(f"/api/transfer-destinations?source_account_id={world['ids']['dst']}").status_code==403

def test_destination_list_is_active_and_same_currency(world):
    client=world["client"](world["ids"]["alice"])
    response=client.get(f"/api/transfer-destinations?source_account_id={world['ids']['src']}")
    assert response.status_code==200
    assert {row["id"] for row in response.json()}=={str(world["ids"]["dst"]),str(world["ids"]["orphan_account"])}

def test_manager_created_customer_profile_links_to_existing_user_and_account(world):
    manager=world["client"](world["ids"]["manager"])
    created=manager.post("/api/customers",json={"full_name":"Dave Demo","email":"dave@example.test"})
    assert created.status_code==201,created.text
    customer_id=created.json()["id"]
    with world["factory"].begin() as db:
        profile=db.get(Customer,__import__('uuid').UUID(customer_id)); profile.status="active"
        db.add(KYCRecord(customer_id=profile.id,document_type="synthetic-demo",synthetic_reference="DEMO-DAVE-APPROVED",status="approved"))
    opened=manager.post("/api/accounts",json={"customer_id":customer_id,"currency":"USD","account_type":"checking"})
    assert opened.status_code==201,opened.text
    dave=world["client"](world["ids"]["dave"])
    accounts=dave.get("/api/accounts")
    assert accounts.status_code==200
    assert opened.json()["id"] in {row["id"] for row in accounts.json()}
    assert str(world["ids"]["orphan_account"]) not in {row["id"] for row in accounts.json()}

def test_manager_can_safely_link_existing_orphan_demo_profile(world):
    manager=world["client"](world["ids"]["manager"])
    response=manager.post(f"/api/customers/{world['ids']['orphan_customer']}/link-application-user")
    assert response.status_code==200,response.text
    charlie=world["client"](world["ids"]["charlie"])
    visible=charlie.get("/api/accounts").json()
    assert {row["id"] for row in visible}=={str(world["ids"]["orphan_account"])}

def test_unlinked_profile_is_diagnosed_without_exposing_it_to_customer(world):
    manager=world["client"](world["ids"]["manager"])
    response=manager.get(f"/api/admin/customers/{world['ids']['orphan_customer']}/transfer-diagnostics")
    assert response.status_code==200,response.text
    result=response.json()
    assert result["application_user_id"] is None
    assert result["profile_link_status"]=="unique_email_match_available"
    assert result["kyc"]["approved"] is True
    assert result["accounts"][0]["ineligibility_reasons"]==["customer_profile_not_linked_to_application_user"]
    user_diagnostic=manager.get(f"/api/admin/users/{world['ids']['charlie']}/transfer-diagnostics").json()
    assert user_diagnostic["application_user_email"]=="charlie@example.test"
    assert user_diagnostic["profiles"][0]["accounts"][0]["ineligibility_reasons"]==["customer_profile_not_linked_to_application_user","account_not_attached_to_signed_in_customer_profile"]
    charlie=world["client"](world["ids"]["charlie"])
    assert charlie.get("/api/accounts").json()==[]

@pytest.mark.parametrize("status",["frozen","closed"])
def test_inactive_customer_source_account_is_ineligible(world,status):
    with world["factory"].begin() as db: db.get(Account,world["ids"]["src"]).status=status
    client=world["client"](world["ids"]["alice"])
    source=client.get("/api/accounts").json()[0]
    assert source["eligible_for_transfer_source"] is False
    assert f"account_{status}" in source["transfer_source_ineligibility_reasons"]
    response=client.post("/api/transfers",json=payload(world["ids"],amount="1.00"),headers={"Idempotency-Key":f"inactive-{status}-001"})
    assert response.status_code==409

def test_zero_ledger_balance_is_not_an_eligible_source(world):
    with world["factory"].begin() as db:
        source=db.get(Account,world["ids"]["src"]); source.balance=Decimal("0.00")
        for entry in db.query(LedgerEntry).filter_by(account_id=source.id): db.delete(entry)
    client=world["client"](world["ids"]["alice"])
    source=client.get("/api/accounts").json()[0]
    assert source["available_balance"]=="0.00"
    assert source["eligible_for_transfer_source"] is False
    assert "no_posted_ledger_funds" in source["transfer_source_ineligibility_reasons"]
    assert client.get(f"/api/transfer-destinations?source_account_id={world['ids']['src']}").status_code==409
    response=client.post("/api/transfers",json=payload(world["ids"],amount="0.01"),headers={"Idempotency-Key":"zero-balance-001"})
    assert response.status_code==409

def test_ledger_projection_mismatch_is_diagnosed_and_transfer_is_rejected(world):
    with world["factory"].begin() as db: db.get(Account,world["ids"]["src"]).balance=Decimal("99.00")
    client=world["client"](world["ids"]["alice"])
    source=client.get("/api/accounts").json()[0]
    assert source["available_balance"]=="100.00"
    assert source["eligible_for_transfer_source"] is False
    assert "ledger_balance_projection_mismatch" in source["transfer_source_ineligibility_reasons"]
    response=client.post("/api/transfers",json=payload(world["ids"],amount="1.00"),headers={"Idempotency-Key":"ledger-mismatch-001"})
    assert response.status_code==409
    with world["factory"]() as db: assert db.get(Account,world["ids"]["src"]).balance==Decimal("99.00")

def test_missing_fields_and_missing_idempotency_header_are_422(world):
    client=world["client"](world["ids"]["alice"])
    missing=client.post("/api/transfers",json={})
    assert missing.status_code==422
    assert {tuple(e["loc"]) for e in missing.json()["detail"]} >= {("body","source_account_id"),("body","destination_account_id"),("body","amount"),("body","currency"),("header","Idempotency-Key")}

@pytest.mark.parametrize("amount,destination,currency,expected",[("100.01",None,"USD",409),("5.00",None,"EUR",422),("5.00","frozen","USD",409),("5.00","closed","USD",409)])
def test_funds_currency_and_status_checks(world,amount,destination,currency,expected):
    if destination in {"frozen","closed"}:
        with world["factory"].begin() as db: db.get(Account,world["ids"]["dst"]).status=destination
        destination=None
    target=world["ids"]["eur"] if currency=="EUR" else world["ids"]["dst"]
    client=world["client"](world["ids"]["alice"])
    response=client.post("/api/transfers",json=payload(world["ids"],destination=target,amount=amount,currency=currency),headers={"Idempotency-Key":f"validation-{uuid4()}"})
    assert response.status_code==expected,response.text
    if expected!=201:
        with world["factory"]() as db: assert db.get(Account,world["ids"]["src"]).balance==Decimal("100.00")

def test_repeated_key_balances_ledger_and_audit_once(world):
    client=world["client"](world["ids"]["alice"])
    body=payload(world["ids"],amount="12.34"); headers={"Idempotency-Key":"repeat-transfer-001"}
    first=client.post("/api/transfers",json=body,headers=headers)
    second=client.post("/api/transfers",json=body,headers=headers)
    assert first.status_code==second.status_code==201
    assert first.json()["id"]==second.json()["id"]
    with world["factory"]() as db:
        assert db.get(Account,world["ids"]["src"]).balance==Decimal("87.66")
        transfer_id=__import__('uuid').UUID(first.json()["id"])
        tx=db.query(LedgerTransaction).filter_by(transfer_id=transfer_id).one()
        entries=db.query(LedgerEntry).filter_by(transaction_id=tx.id).all()
        assert len(entries)==2
        assert sum((e.amount for e in entries if e.direction=="debit"),Decimal("0"))==sum((e.amount for e in entries if e.direction=="credit"),Decimal("0"))
        assert db.query(AuditLog).filter_by(action="transfer.posted",entity_id=str(transfer_id)).count()==1

def test_concurrent_requests_cannot_overspend(world):
    with world["factory"].begin() as db: db.get(Account,world["ids"]["src"]).balance=Decimal("100.00")
    client=world["client"](world["ids"]["alice"])
    body=payload(world["ids"],amount="70.00")
    def send(key): return client.post("/api/transfers",json=body,headers={"Idempotency-Key":key}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool: statuses=list(pool.map(send,["concurrent-withdrawal-1","concurrent-withdrawal-2"]))
    assert sorted(statuses)==[201,409]
    with world["factory"]() as db: assert db.get(Account,world["ids"]["src"]).balance==Decimal("30.00")
