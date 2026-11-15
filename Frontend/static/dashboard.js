const deleteLocalData = document.querySelector("#deleteLocalData");
const dataActionStatus = document.querySelector("#dataActionStatus");

deleteLocalData?.addEventListener("click", async () => {
  if (!window.confirm("Delete all locally stored arcade rounds? This cannot be undone.")) return;

  dataActionStatus.textContent = "";
  dataActionStatus.classList.remove("error");

  try {
    const response = await fetch("/api/samples", { method: "DELETE" });
    if (!response.ok) throw new Error(`Request failed (${response.status}).`);
    dataActionStatus.textContent = "Local arcade rounds deleted.";
  } catch (error) {
    dataActionStatus.classList.add("error");
    dataActionStatus.textContent = error instanceof Error ? error.message : "Could not delete local arcade rounds.";
  }
});
