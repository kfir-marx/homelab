import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  metadataBase: new URL("https://tapy.547600.xyz"),
  title: "Tapy — Travel Upsell Workspace",
  description:
    "Tapy helps travel professionals identify flights without matching hotels and manage reviewed hotel-upsell opportunities.",
  alternates: {
    canonical: "/",
  },
  openGraph: {
    title: "Tapy — Travel Upsell Workspace",
    description:
      "Identify confirmed travel bookings, find hotel opportunities, and manage agent-reviewed outreach.",
    type: "website",
    url: "/",
    siteName: "Tapy",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
