import { formatAgo, formatTime } from "./format";

type DeviceRow = {
  id: string;
  name: string | null;
  last_seen: string | null;
  revoked: boolean;
  stale: boolean;
};

const $ = <T extends HTMLElement>(selector: string) => document.querySelector<T>(selector);

function responseError(payload: unknown, status: number) {
  if (payload && typeof payload === "object" && "error" in payload && typeof payload.error === "string") {
    return payload.error;
  }
  return `Request failed (${status}).`;
}

function setStatus(message: string, isError = false) {
  const status = $("#deviceListStatus");
  if (!status) return;
  status.textContent = message;
  status.classList.toggle("error", isError);
}

const deviceName = (device: DeviceRow) => (device.name && device.name.length > 0 ? device.name : "Unnamed device");

function renderDevices(devices: DeviceRow[]) {
  const list = $("#deviceList");
  if (!list) return;
  list.replaceChildren();
  setStatus(
    devices.length === 0
      ? "No paired devices. Pair one from Setup."
      : `${devices.length} paired device${devices.length === 1 ? "" : "s"}.`,
  );
  for (const device of devices) {
    const item = document.createElement("li");
    item.className = "alert-item";
    const title = document.createElement("strong");
    title.textContent = deviceName(device);
    const body = document.createElement("p");
    const seen = device.last_seen ? `Last check-in ${formatAgo(device.last_seen)}.` : "No check-in has been recorded.";
    const presence = device.revoked
      ? "Revoked. The agent's connection no longer works."
      : device.stale
        ? "Offline."
        : "Connected.";
    body.textContent = `${seen} ${presence}`;
    if (device.last_seen) body.title = formatTime(device.last_seen);
    item.append(title, body);
    if (!device.revoked) {
      const actions = document.createElement("div");
      actions.className = "alert-actions";
      const button = document.createElement("button");
      button.type = "button";
      button.className = "button secondary-button";
      button.textContent = "Revoke";
      button.addEventListener("click", () => {
        const confirmed = window.confirm(
          `Revoke ${deviceName(device)}? Its agent stops sending data and needs a new pairing code to reconnect.`,
        );
        if (!confirmed) return;
        button.disabled = true;
        void revokeDevice(device, button);
      });
      actions.append(button);
      item.append(actions);
    }
    list.append(item);
  }
}

async function revokeDevice(device: DeviceRow, button: HTMLButtonElement) {
  try {
    const response = await fetch(`/api/devices/${encodeURIComponent(device.id)}/revoke`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (!response.ok) throw new Error(responseError(await response.json().catch(() => null), response.status));
    await refreshDevices();
    setStatus(`${deviceName(device)} was revoked.`);
  } catch (error) {
    // Let the user try again instead of leaving the button stuck.
    button.disabled = false;
    setStatus(
      error instanceof TypeError
        ? "Could not revoke that device. The server did not respond."
        : error instanceof Error
          ? error.message
          : "Could not revoke that device.",
      true,
    );
  }
}

async function refreshDevices() {
  let response: Response;
  try {
    response = await fetch("/api/devices");
  } catch {
    setStatus("Could not load devices. The server did not respond.", true);
    return;
  }
  if (response.status === 404) {
    setStatus("Device list is not available on this server.");
    return;
  }
  if (!response.ok) {
    setStatus(`Could not load devices (${response.status}).`, true);
    return;
  }
  renderDevices(((await response.json()) as { devices: DeviceRow[] }).devices ?? []);
}

void refreshDevices();
