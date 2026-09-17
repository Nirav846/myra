/**
 * Shared debug flag — reads from the same localStorage key used by
 * SettingsContext / DebugPanel.  Returns true only when the user has
 * enabled "Debug mode" in the UI settings panel.
 */
export function isDebug(): boolean {
  try {
    const raw = localStorage.getItem('myra_ui_settings');
    return raw ? JSON.parse(raw).debugMode === true : false;
  } catch {
    return false;
  }
}
