import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import KiprisSettings from './KiprisSettings';
import { api } from '../lib/api';
import type { AppSettings } from '../lib/types';

vi.mock('../lib/api', () => ({ api: {
  updateSettings: vi.fn(), settings: vi.fn(), checkKipris: vi.fn(), updateKiprisUsage: vi.fn(),
} }));
const settings = {
  values: { kipris_integration_enabled: true, kipris_api_key: '' },
  secrets_set: { kipris_api_key: true },
  kipris_quota: { month: '2026-09', requests: 90, external_requests: 710,
    used: 800, limit: 1000, remaining: 200, reset_at: '2026-10-01T00:00:00+09:00',
    blocked: false, warning: true, history: [{ month: '2026-09', requests: 90, external_requests: 710 }] },
} as unknown as AppSettings;

describe('키프리스 설정', () => {
  beforeEach(() => { vi.clearAllMocks(); vi.mocked(api.updateSettings).mockResolvedValue(settings); });
  afterEach(cleanup);
  it('shows count quota and KST reset date even when disabled', () => {
    render(<KiprisSettings settings={{ ...settings, values: { ...settings.values, kipris_integration_enabled: false } }} onChange={vi.fn()} />);
    expect(screen.getByText('800 / 1,000회')).toBeTruthy();
    expect(screen.getByText('200회')).toBeTruthy();
    expect(screen.getByText(/2026\. 10\. 1\./)).toBeTruthy();
    expect((screen.getByRole('button', { name: /연결 테스트/ }) as HTMLButtonElement).disabled).toBe(true);
  });
  it('saves a password draft and clears it without returning the secret', async () => {
    const onChange = vi.fn();
    render(<KiprisSettings settings={settings} onChange={onChange} />);
    const input = screen.getByLabelText(/KIPRIS Plus API 키/) as HTMLInputElement;
    expect(input.type).toBe('password');
    fireEvent.change(input, { target: { value: 'new-private-key' } });
    fireEvent.click(screen.getByRole('button', { name: '키 저장' }));
    await waitFor(() => expect(api.updateSettings).toHaveBeenCalledWith({ kipris_api_key: 'new-private-key' }));
    await waitFor(() => expect(input.value).toBe(''));
    expect(onChange).toHaveBeenCalledWith(settings);
  });
  it('tests saved credentials and refreshes consumed quota on an API failure', async () => {
    vi.mocked(api.checkKipris).mockResolvedValue({ ok: false, detail: '서비스 승인 필요', http_status: 200, expires_in: null });
    vi.mocked(api.settings).mockResolvedValue(settings);
    render(<KiprisSettings settings={settings} onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /연결 테스트/ }));
    await screen.findByText('서비스 승인 필요');
    expect(api.settings).toHaveBeenCalledTimes(1);
  });
  it('blocks tests at the limit and reconciles usage with the displayed month', async () => {
    vi.mocked(api.updateKiprisUsage).mockResolvedValue(settings);
    render(<KiprisSettings settings={{ ...settings, kipris_quota: { ...settings.kipris_quota!, blocked: true } }} onChange={vi.fn()} />);
    expect((screen.getByRole('button', { name: /연결 테스트/ }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/포털에서 확인한/), { target: { value: '880' } });
    fireEvent.click(screen.getByRole('button', { name: '사용량 반영' }));
    await waitFor(() => expect(api.updateKiprisUsage).toHaveBeenCalledWith('2026-09', 880));
  });
});
