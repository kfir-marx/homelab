export type Lang = "en" | "he";
export const LOCALES: Record<Lang, string> = { en: "en-US", he: "he-IL" };

const en = {
  "notifications.aria": "Open notifications",
  "notifications.title": "Notifications",
  "notifications.subtitle": "Background work and booking updates",
  "notifications.empty": "No activity yet.",
  "notifications.activity": "Background activity",
  "notifications.updates": "Updates",
  "notifications.readError": "Could not mark notifications read",
  "notifications.tryAgain": "Try again.",
  "tip.aria": "More information",
  "toast.aria.dismiss": "Dismiss",
};

const he: typeof en = {
  "notifications.aria": "פתיחת התראות",
  "notifications.title": "התראות",
  "notifications.subtitle": "פעילות ברקע ועדכוני הזמנות",
  "notifications.empty": "אין פעילות עדיין.",
  "notifications.activity": "פעילות ברקע",
  "notifications.updates": "עדכונים",
  "notifications.readError": "לא ניתן לסמן את ההתראות כנקראו",
  "notifications.tryAgain": "נסו שוב.",
  "tip.aria": "מידע נוסף",
  "toast.aria.dismiss": "סגירה",
};

export const DICTIONARIES = { en, he };

export function tr(
  dictionary: Record<string, string>,
  key: string,
  variables: Record<string, string | number> = {},
): string {
  const template = dictionary[key] || DICTIONARIES.en[key as keyof typeof en] || key;
  return Object.entries(variables).reduce(
    (value, [name, replacement]) => value.replaceAll(`{${name}}`, String(replacement)),
    template,
  );
}
