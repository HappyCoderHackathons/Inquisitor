import { createServer } from "node:http";
import { mongodbAdapter } from "@better-auth/mongo-adapter";
import { toNodeHandler } from "better-auth/node";
import { MongoClient } from "mongodb";
import { createAuth } from "./config.mts";

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`Missing ${name}`);
  return value;
}

const client = new MongoClient(required("MONGODB_URI"));
await client.connect();
const auth = createAuth(
  mongodbAdapter(client.db(required("MONGODB_DB")), { client }),
  required("BETTER_AUTH_URL"),
  required("BETTER_AUTH_SECRET"),
);
createServer(toNodeHandler(auth)).listen(3001, "127.0.0.1", () => {
  console.log("Authentication service listening on 127.0.0.1:3001");
});
