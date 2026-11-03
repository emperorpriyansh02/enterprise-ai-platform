import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { AuthGate, AuthProvider } from "@/components/auth-provider";
import NavBar from "@/components/nav";
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
  title: "Enterprise AI Platform",
  description: "Multi-provider AI gateway: chat, prompts, knowledge bases and tools",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="flex h-dvh flex-col">
        <AuthProvider>
          <NavBar />
          <div className="flex min-h-0 flex-1 flex-col">
            <AuthGate>{children}</AuthGate>
          </div>
        </AuthProvider>
      </body>
    </html>
  );
}
