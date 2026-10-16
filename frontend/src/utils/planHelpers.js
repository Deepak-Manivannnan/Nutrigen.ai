/**
 * Shared plan-display helpers (single home for logic that used to be
 * copy-pasted across PlansPage, GroceryPage and PlanSelect).
 */

/**
 * Newest-first plan order: latest created_at first, bigger id breaks ties
 * (so "recent" tracks creation, and the newest plan loads by default).
 * Never mutates the input array.
 */
export function sortPlansNewestFirst(plans) {
  return (Array.isArray(plans) ? plans : []).slice().sort((a, b) => {
    const ta = new Date(a.created_at || 0).getTime();
    const tb = new Date(b.created_at || 0).getTime();
    if (ta !== tb) return tb - ta;
    return (b.id || 0) - (a.id || 0);
  });
}

/**
 * Whether an ingredient name refers to something measured in ml/L rather
 * than g/kg. Word-boundary match, so "boiled egg" is NOT liquid (unlike a
 * plain substring check, where "oil" matches "boiled"), while "soy milk",
 * "olive oil" and "coconut milk" correctly are.
 */
const LIQUID_WORDS = [
  'milk', 'juice', 'water', 'oil', 'broth', 'stock', 'sauce',
  'vinegar', 'syrup', 'honey', 'dressing', 'buttermilk', 'tea', 'coffee',
];
const LIQUID_PATTERN = new RegExp(`\\b(${LIQUID_WORDS.join('|')})\\b`, 'i');

export function isLiquidIngredient(name) {
  if (!name) return false;
  return LIQUID_PATTERN.test(String(name));
}
