"use strict";
(() => {
  // frontend/nav.ts
  var signOut = document.getElementById("navSignOut");
  signOut?.addEventListener("click", async () => {
    signOut.disabled = true;
    try {
      const response = await fetch("/api/auth/sign-out", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}"
      });
      if (!response.ok) throw new Error(String(response.status));
      window.location.assign("/");
    } catch {
      signOut.disabled = false;
      signOut.textContent = "Sign out failed. Try again";
    }
  });
})();
