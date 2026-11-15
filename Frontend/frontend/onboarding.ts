const element = (id: string) => document.getElementById(id);

async function post(path: string) {
  return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
}

element("createCode")?.addEventListener("click", async () => {
  const response = await post("/api/pairing-codes");
  const target = element("pairingCode");
  if (!target) return;
  if (!response.ok) {
    target.textContent = `Could not create a code (${response.status}).`;
    return;
  }
  target.textContent = ((await response.json()) as { code: string }).code;
});
