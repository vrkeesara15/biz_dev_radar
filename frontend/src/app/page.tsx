import { redirect } from "next/navigation";

import { auth } from "@/auth";

export default async function IndexPage() {
  const session = await auth();
  redirect(session?.user ? "/app" : "/signin");
}
