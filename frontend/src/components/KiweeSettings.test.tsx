import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import type { AppSettings, KiweeTrialResult } from '../lib/types';
import KiweeSettings from './KiweeSettings';

vi.mock('../lib/api', () => ({ api: { updateSettings: vi.fn(), searchKiwee: vi.fn() } }));
const settings = { values: { kiwee_integration_enabled: true,
  kiwee_endpoint: 'https://gateway.kiwee.or.kr/solr/select', kiwee_shards: 'shd_kr' } } as AppSettings;
const success: KiweeTrialResult = { ok: true, detail: '전체 12건 중 5건을 가져왔습니다.',
  http_status: 200, error_code: null, solr_query: 'tl:"반도체"', total_found: 12, returned: 5,
  returned_fields: ['pn_s', 'tl'], notes: [], next_page: 2,
  records: [{ document_number: 'KR1020230012345A', fields: { title: '반도체 센서' } }] };

describe('Kiwee 시험 검색', () => {
  beforeEach(() => { vi.clearAllMocks(); });
  afterEach(cleanup);
  it('requires saving changed settings before searching', async () => {
    const onChange = vi.fn();
    vi.mocked(api.updateSettings).mockResolvedValue(settings);
    render(<KiweeSettings settings={settings} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('검색 범위'), { target: { value: 'shd_us' } });
    expect((screen.getByRole('button', { name: 'Kiwee 시험 검색' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Kiwee 설정 저장' }));
    await waitFor(() => expect(api.updateSettings).toHaveBeenCalledWith(expect.objectContaining({ kiwee_shards: 'shd_us' })));
    expect(onChange).toHaveBeenCalledWith(settings);
    expect(api.searchKiwee).not.toHaveBeenCalled();
  });
  it('shows actual records and requests the next page', async () => {
    vi.mocked(api.searchKiwee).mockResolvedValue(success);
    render(<KiweeSettings settings={settings} onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Kiwee 시험 검색' }));
    await screen.findByText('반도체 센서');
    expect(api.searchKiwee).toHaveBeenCalledWith('반도체', 'keywords', 1);
    fireEvent.click(screen.getByRole('button', { name: '다음 5건' }));
    await waitFor(() => expect(api.searchKiwee).toHaveBeenLastCalledWith('반도체', 'keywords', 2));
  });
  it('shows authentication failure without a zero-result claim', async () => {
    vi.mocked(api.searchKiwee).mockResolvedValue({ ...success, ok: false, detail: '클라이언트 인증서가 필요합니다.',
      error_code: 'KIWEE.AUTH', http_status: 403, total_found: null, records: [], returned: 0, next_page: null });
    render(<KiweeSettings settings={settings} onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Kiwee 시험 검색' }));
    await screen.findByText(/클라이언트 인증서가 필요합니다/);
    expect(screen.queryByText(/전체 0건/)).toBeNull();
    expect(screen.queryByRole('button', { name: '다음 5건' })).toBeNull();
    expect(screen.getByText('오류 코드: KIWEE.AUTH')).toBeTruthy();
  });
});
