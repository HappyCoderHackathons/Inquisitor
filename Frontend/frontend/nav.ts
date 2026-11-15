// Shared header: the Sign out button shown on every page when signed in.
const signOut = document.getElementById("navSignOut") as HTMLButtonElement | null;
signOut?.addEventListener("click", async () => {
  signOut.disabled = true;
  try {
    const response = await fetch("/api/auth/sign-out", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (!response.ok) throw new Error(String(response.status));
    window.location.assign("/");
  } catch {
    signOut.disabled = false;
    signOut.textContent = "Sign out failed. Try again";
  }
});
