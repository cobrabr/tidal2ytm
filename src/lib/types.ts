export interface ProviderState {
	valid: boolean;
	expires_at: string | null;
}

export interface AuthStatus {
	tidal: ProviderState;
	ytm: ProviderState;
}
