export type TransferSourceAccount = {
  id: string;
  account_number: string;
  account_type: string;
  currency: string;
  balance: string;
  status: string;
  customer_status: string;
  available_balance: string;
  eligible_for_transfer_source: boolean;
};

export function eligibleTransferSources(accounts: TransferSourceAccount[] | undefined): TransferSourceAccount[];
export function chooseTransferSource(accounts: TransferSourceAccount[] | undefined, currentId: string, autoSelectSingle: boolean): string;
export function explainNoEligibleSource(accounts: TransferSourceAccount[] | undefined, me: {customer_profile?: {id:string; status:string; kyc_status:string; kyc_approved:boolean} | null; unlinked_profile_match_count?:number} | undefined): string;
