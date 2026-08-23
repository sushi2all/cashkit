export interface Money {
  exact: string;
  display: string;
}
export interface MoneyMove {
  before: Money | null;
  after: Money | null;
  change: Money | null;
}
export declare const m: Money;
export declare const n: Money;
export declare const move: MoneyMove;
export declare const label: string;
