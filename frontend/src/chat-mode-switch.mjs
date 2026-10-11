export function completePersonalLogin(credential, persistMode, notifyRoot) {
  persistMode("personal", credential);
  notifyRoot("personal");
}
