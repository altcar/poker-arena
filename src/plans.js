// Shared plan limits. Kept separate so the Worker and billing module can both
// import them without a cycle.
export const PLAN_LIMITS = {
  open: { bots: 1, label: "Open" },
  pro: { bots: 5, label: "Pro" },
  studio: { bots: 25, label: "Studio" },
};

export const PLAN_PRICES = { pro: 19, studio: 99 };
export const PLAN_PAPER_CREDIT = { pro: 25000, studio: 100000 };
export const PAPER_STARTING_BALANCE = 10000;
