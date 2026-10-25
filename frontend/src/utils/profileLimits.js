/**
 * Allowed ranges for the body measurements on a profile.
 *
 * Used by the onboarding form and the Edit Profile dialog so both accept exactly
 * the same values. The backend enforces the same numbers (see AGE_MIN etc. in
 * backend/app/schemas/api.py) - change them in both places together.
 */
export const PROFILE_LIMITS = {
  age: { min: 18, max: 120, label: 'Age', unit: 'years' },
  height_cm: { min: 100, max: 250, label: 'Height', unit: 'cm' },
  weight_kg: { min: 30, max: 300, label: 'Weight', unit: 'kg' },
};

/**
 * Returns a short message when `value` is not an acceptable number for `field`
 * ('age' | 'height_cm' | 'weight_kg'), or '' when it is fine. An empty value is
 * "not filled in yet" and returns '' - required-ness is checked separately.
 */
export function validateProfileField(field, value) {
  const limit = PROFILE_LIMITS[field];
  if (!limit || value === '' || value === null || value === undefined) return '';
  const n = Number(value);
  if (!Number.isFinite(n)) return `${limit.label} must be a number.`;
  if (field === 'age' && !Number.isInteger(n)) return 'Age must be a whole number.';
  if (n < limit.min || n > limit.max) {
    return field === 'age'
      ? `Age must be between ${limit.min} and ${limit.max}.`
      : `${limit.label} must be between ${limit.min} and ${limit.max} ${limit.unit}.`;
  }
  return '';
}
