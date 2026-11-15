import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import MissionConsole from "./MissionConsole";

export default async function MissionPage() {
  const session = (await cookies()).get("orbitalguard_session");
  if (!session) redirect("/login");

  const backend = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";
  const response = await fetch(`${backend}/api/auth/me`, {
    headers: { cookie: `orbitalguard_session=${session.value}` },
    cache: "no-store",
  }).catch(() => null);
  if (response?.status === 401) redirect("/login");
  if (!response?.ok) {
    return <main className="p-6" role="status">Mission control is unavailable. Refresh to try again.</main>;
  }
  const operator: { id: string; name: string } = await response.json();
  return <MissionConsole operator={operator} />;
}
