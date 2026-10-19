/**
 * Single source of truth for the allergen categories the app collapses typed
 * foods into.
 *
 * There used to be two copies of this table — one in the Edit Profile modal and
 * none at all in onboarding — so the same typed food could be resolved in one
 * screen and kept verbatim in the other. Both now import from here.
 *
 * `resolveCategory` only recognises exact aliases ("prawns", not "prawn soup").
 * Anything it does not recognise is the caller's to keep as a chip, which is
 * the behaviour we want: a food we are unsure about must never be silently
 * folded into a category the user did not pick.
 */

export const COMMON_ALLERGENS = ['Nuts', 'Shellfish', 'Dairy', 'Gluten', 'Eggs', 'Soy', 'Fish'];

export const CATEGORY_ALIASES = {
  'Nuts': ['nut', 'nuts', 'peanut', 'peanuts', 'groundnut', 'groundnuts', 'tree nut', 'tree nuts', 'almond', 'almonds', 'walnut', 'walnuts', 'cashew', 'cashews', 'pistachio', 'pistachios', 'pecan', 'pecans', 'hazelnut', 'hazelnuts', 'macadamia', 'praline'],
  'Shellfish': ['shellfish', 'shrimp', 'shrimps', 'prawn', 'prawns', 'crab', 'crabs', 'lobster', 'lobsters', 'clam', 'clams', 'mussel', 'mussels', 'oyster', 'oysters', 'scallop', 'scallops', 'squid', 'calamari', 'octopus', 'crawfish', 'crayfish'],
  'Dairy': ['dairy', 'milk', 'cow milk', 'cheese', 'butter', 'cream', 'yoghurt', 'yogurt', 'curd', 'paneer', 'ghee', 'casein', 'whey', 'lactose'],
  'Gluten': ['gluten', 'wheat', 'barley', 'rye', 'spelt', 'seitan', 'semolina', 'couscous', 'maida', 'atta', 'bread'],
  'Eggs': ['egg', 'eggs', 'mayo', 'mayonnaise', 'albumen', 'albumin', 'meringue', 'omelette', 'omelet'],
  'Soy': ['soy', 'soya', 'soybean', 'soybeans', 'edamame', 'tofu', 'tempeh', 'miso', 'tamari'],
  'Fish': ['fish', 'salmon', 'tuna', 'cod', 'tilapia', 'trout', 'halibut', 'sardine', 'anchovy', 'mackerel', 'bass', 'snapper'],
};

/**
 * Fold a single typed food into its category, or null when it belongs to none.
 * Case- and whitespace-insensitive on the input; the returned value is always
 * one of COMMON_ALLERGENS so the caller can compare case-insensitively.
 */
export function resolveCategory(value) {
  const clean = String(value || '').trim().toLowerCase();
  if (!clean) return null;
  return COMMON_ALLERGENS.find(cat =>
    (CATEGORY_ALIASES[cat] || []).some(alias => alias === clean)
  ) || null;
}

/** True when the value already names one of the seven categories. */
export function isPredefinedAllergy(value) {
  const clean = String(value || '').trim().toLowerCase();
  return COMMON_ALLERGENS.some(p => p.toLowerCase() === clean);
}

/**
 * Normalise a list of allergens that is already stored, so raw foods that were
 * saved before categorisation existed (or while the server's LLM normaliser was
 * unavailable) light up their category instead of lingering as chips.
 *
 * Also de-duplicates, because "prawns" and "Shellfish" can otherwise both be
 * present once a raw entry is folded into its category.
 */
export function resolveAllergyList(list) {
  if (!Array.isArray(list)) return [];
  const seen = new Set();
  const out = [];
  for (const raw of list) {
    const trimmed = String(raw || '').trim();
    if (!trimmed) continue;
    const category = resolveCategory(trimmed);
    const value = category || trimmed;
    const key = value.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(value);
  }
  return out;
}

/**
 * True when two allergy lists hold the same set, ignoring case and order.
 * Used to skip the server-side normaliser round trip when nothing changed.
 */
export function sameAllergySet(a, b) {
  const norm = list => new Set((Array.isArray(list) ? list : [])
    .map(x => String(x || '').trim().toLowerCase())
    .filter(Boolean));
  const sa = norm(a), sb = norm(b);
  if (sa.size !== sb.size) return false;
  for (const v of sa) if (!sb.has(v)) return false;
  return true;
}
