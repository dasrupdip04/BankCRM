export function eligibleTransferSources(accounts) {
  return (Array.isArray(accounts) ? accounts : []).filter(
    (account) => account.status === 'active'
      && account.customer_status === 'active'
      && account.account_type !== 'system_clearing'
      && account.eligible_for_transfer_source === true
      && Number(account.available_balance) > 0,
  );
}

export function chooseTransferSource(accounts, currentId, autoSelectSingle) {
  const eligible = eligibleTransferSources(accounts);
  if (currentId && eligible.some((account) => account.id === currentId)) return currentId;
  return autoSelectSingle && eligible.length === 1 ? eligible[0].id : '';
}

export function explainNoEligibleSource(accounts, me) {
  if (!me?.customer_profile && me?.unlinked_profile_match_count === 1) {
    return 'A customer profile matches your signed-in email but is not linked to your application user. Ask a manager to use Link signed-in user; your KYC does not need to be resubmitted.';
  }
  if (!me?.customer_profile && me?.unlinked_profile_match_count > 1) {
    return 'Several unlinked profiles match your signed-in email. Ask a manager to resolve the duplicate profile links.';
  }
  if (!me?.customer_profile) return 'No customer profile is linked to your signed-in account. Create your profile or ask a manager to check the profile link.';

  const rows = Array.isArray(accounts) ? accounts : [];
  if (rows.length) {
    const messages=[];
    for (const account of rows) {
      const reasons = account.transfer_source_ineligibility_reasons || [];
      if (reasons.includes('system_clearing_account')) messages.push(`Account ${account.account_number} is a clearing account and cannot be used as a transfer source.`);
      else if (reasons.includes('account_frozen')) messages.push(`Account ${account.account_number} is frozen. Ask a manager to review its status.`);
      else if (reasons.includes('account_closed')) messages.push(`Account ${account.account_number} is closed and cannot be used for transfers.`);
      else if (reasons.includes('account_inactive')) messages.push(`Account ${account.account_number} is inactive. Ask a manager to review its status.`);
      else if (reasons.includes('ledger_balance_projection_mismatch')) messages.push(`Account ${account.account_number} balance does not match its posted ledger and needs manager review.`);
      else if (reasons.includes('customer_profile_not_active')) messages.push(`Account ${account.account_number} belongs to a customer profile that is ${me.customer_profile?.status || 'not active'}; a manager must review its KYC and profile status.`);
      else if (reasons.includes('no_posted_ledger_funds')) messages.push(`Account ${account.account_number} has no available funds in its posted ledger. Ask a manager to use the authorized funding workflow; no funds were added automatically.`);
    }
    if (messages.length) return messages.join(' ');
  }
  if (me.customer_profile.kyc_status !== 'approved') return `Your KYC status is ${me.customer_profile.kyc_status}. Follow the onboarding status shown in Profile & KYC.`;
  return 'No account is attached to your approved customer profile. Ask a manager to inspect the account and profile ownership links; do not resubmit KYC.';
}
