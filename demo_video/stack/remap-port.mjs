// Demo-only: Windows reserves 3001 on this PC, so the auth service listens on 13001 instead.
import http from "node:http";
const listen = http.Server.prototype.listen;
http.Server.prototype.listen = function (port, ...rest) {
  return listen.call(this, port === 3001 ? 13001 : port, ...rest);
};
