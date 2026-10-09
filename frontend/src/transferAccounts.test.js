import test from 'node:test';
import assert from 'node:assert/strict';
import { chooseTransferSource, eligibleTransferSources, explainNoEligibleSource } from './transferAccounts.js';

const active = (id, overrides = {}) => ({
  id,
  account_number: `ACCT-${id}`,
  account_type: 'checking',
  currency: 'USD',
  balance: '25.00',
  status: 'active',
  customer_status: 'active',
  available_balance: '25.00',
  eligible_for_transfer_source: true,
  ...overrides,
});

test('auto-selects the only eligible source account for a customer', () => {
  const sources = [active('own'), active('frozen', { status: 'frozen' }), active('clearing', { account_type: 'system_clearing' })];
  assert.deepEqual(eligibleTransferSources(sources).map((account) => account.id), ['own']);
  assert.equal(chooseTransferSource(sources, '', true), 'own');
});

test('keeps multiple eligible accounts unselected until the customer chooses one', () => {
  const sources = [active('one'), active('two')];
  assert.equal(chooseTransferSource(sources, '', true), '');
  assert.equal(chooseTransferSource(sources, 'two', true), 'two');
});

test('returns no source when no accounts are eligible', () => {
  const sources = [active('closed', { status: 'closed' }), active('clearing', { account_type: 'system_clearing' }), active('pending-kyc', { customer_status: 'pending' }), active('unfunded', { available_balance: '0.00', eligible_for_transfer_source: false })];
  assert.equal(chooseTransferSource(sources, '', true), '');
});

test('explains an unlinked exact-email profile without suggesting KYC resubmission', () => {
  const message = explainNoEligibleSource([], { customer_profile: null, unlinked_profile_match_count: 1 });
  assert.match(message, /not linked/);
  assert.match(message, /does not need to be resubmitted/);
});

test('explains an approved profile with a zero-ledger account as unfunded', () => {
  const account = active('empty', { available_balance: '0.00', eligible_for_transfer_source: false, transfer_source_ineligibility_reasons: ['no_posted_ledger_funds'] });
  const message = explainNoEligibleSource([account], { customer_profile: { id: 'profile', status: 'active', kyc_status: 'approved', kyc_approved: true } });
  assert.match(message, /no available funds in its posted ledger/);
  assert.doesNotMatch(message, /resubmit KYC/i);
});
