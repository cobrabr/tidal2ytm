import { readable } from "svelte/store";

export type RouteId = "home" | "library" | "match" | "review" | "transfer" | "diagnostics";

export const ROUTES: readonly RouteId[] = ["home", "library", "match", "review", "transfer", "diagnostics"];

const initial = (): RouteId => {
	const hash = window.location.hash.replace(/^#\/?/, "");
	return (ROUTES as readonly string[]).includes(hash) ? (hash as RouteId) : "home";
};

export const currentRoute = readable<RouteId>(initial(), (set) => {
	const onChange = (): void => set(initial());
	window.addEventListener("hashchange", onChange);
	return () => window.removeEventListener("hashchange", onChange);
});

export function navigate(route: RouteId): void {
	window.location.hash = `#/${route}`;
}
