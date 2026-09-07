import AppShell from "./components/AppShell";
import { DemoProvider } from "./lib/store";

export default function Home() {
  return (
    <DemoProvider>
      <AppShell />
    </DemoProvider>
  );
}
