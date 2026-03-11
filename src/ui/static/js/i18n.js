/**
 * Open Swiss Tax Statement - Lightweight i18n Module
 *
 * Detects language from localStorage > browser locale > fallback "en".
 * Loads JSON translation files from static/i18n/{lang}.json.
 * Applies translations to DOM elements with data-i18n attributes.
 */

(function () {
  "use strict";

  const SUPPORTED_LANGS = ["en", "de", "fr", "it"];
  const STORAGE_KEY = "opensteuerauszug_lang";

  let currentLang = "en";
  let translations = {};

  /**
   * Detect the best language: localStorage > browser locale > "en"
   */
  function detectLanguage() {
    // 1. Check localStorage
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored && SUPPORTED_LANGS.includes(stored)) {
      return stored;
    }

    // 2. Check browser locale
    const browserLangs = navigator.languages || [navigator.language || "en"];
    for (const lang of browserLangs) {
      const short = lang.substring(0, 2).toLowerCase();
      if (SUPPORTED_LANGS.includes(short)) {
        return short;
      }
    }

    // 3. Fallback
    return "en";
  }

  /**
   * Load translation JSON for the given language.
   */
  async function loadTranslations(lang) {
    try {
      return await pywebview.api.get_translations(lang);
    } catch (err) {
      console.warn(`[i18n] Failed to load ${lang}.json:`, err);
      if (lang !== "en") {
        console.warn("[i18n] Falling back to en.json");
        return loadTranslations("en");
      }
      return {};
    }
  }

  /**
   * Apply translations to all elements with data-i18n attributes.
   */
  function applyTranslations() {
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      const key = el.getAttribute("data-i18n");
      const translated = t(key);
      if (translated !== key) {
        const attr = el.getAttribute("data-i18n-attr");
        if (attr) {
          el.setAttribute(attr, translated);
        } else {
          el.textContent = translated;
        }
      }
    });
  }

  /**
   * Highlight the active language button in the selector.
   */
  function updateLangButtons() {
    document.querySelectorAll(".lang-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.lang === currentLang);
    });
  }

  /**
   * Initialize i18n: detect language, load translations, apply to DOM.
   */
  async function initI18n() {
    currentLang = detectLanguage();
    translations = await loadTranslations(currentLang);
    applyTranslations();
    updateLangButtons();
    document.documentElement.setAttribute("lang", currentLang);
  }

  /**
   * Switch to a new language. Saves to localStorage and reloads.
   */
  function setLanguage(lang) {
    if (!SUPPORTED_LANGS.includes(lang)) return;
    localStorage.setItem(STORAGE_KEY, lang);
    window.location.reload();
  }

  /**
   * Get current language code.
   */
  function getCurrentLang() {
    return currentLang;
  }

  /**
   * Translate a key. Returns the translation or the key itself if not found.
   */
  function t(key) {
    return key in translations ? translations[key] : key;
  }

  // Export to global scope
  window.i18n = {
    t: t,
    initI18n: initI18n,
    setLanguage: setLanguage,
    getCurrentLang: getCurrentLang,
    SUPPORTED_LANGS: SUPPORTED_LANGS,
  };
})();
