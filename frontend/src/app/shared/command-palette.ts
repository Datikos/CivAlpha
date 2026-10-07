import { NgTemplateOutlet } from '@angular/common';
import { httpResource } from '@angular/common/http';
import {
  Component,
  ElementRef,
  computed,
  effect,
  inject,
  model,
  signal,
  viewChild,
} from '@angular/core';
import { Router } from '@angular/router';
import { apiUrl, valueOf } from '../core/api';
import { CompanySummary } from '../core/models';
import { AuthService } from '../core/auth.service';
import { NAV_GROUPS, NavGroup, visibleNav } from '../core/nav';
import { Icon, IconName } from './icon';

interface Item {
  id: string;
  kind: 'page' | 'company' | 'action';
  label: string;
  sub: string;
  url: string;
  icon?: IconName;
}

interface PageItem extends Item {
  haystack: string;
}

const MAX_COMPANIES = 8;
const TICKER = /^[A-Za-z0-9.\-]{1,10}$/;

function pageItems(groups: NavGroup[]): PageItem[] {
  return groups.flatMap((g) =>
    g.links.map((l) => ({
      id: 'p:' + l.path,
      kind: 'page' as const,
      label: l.label,
      sub: g.label,
      url: l.path,
      icon: l.icon,
      haystack: `${l.label} ${g.label} ${l.keywords ?? ''}`.toLowerCase(),
    })),
  );
}

/** ⌘K quick jump: pages and companies, keyboard driven. */
@Component({
  selector: 'app-command-palette',
  imports: [Icon, NgTemplateOutlet],
  template: `
    <dialog
      #dlg
      class="palette"
      aria-label="Jump to a page or company"
      (close)="open.set(false)"
      (click)="backdrop($event)"
    >
      <div class="palette-box">
        <div class="palette-search">
          <app-icon name="search" />
          <input
            #box
            type="text"
            role="combobox"
            aria-expanded="true"
            aria-controls="palette-list"
            [attr.aria-activedescendant]="active() ? 'opt-' + active()!.id : null"
            autocomplete="off"
            spellcheck="false"
            placeholder="Jump to a page or company…"
            [value]="q()"
            (input)="onInput($any($event.target).value)"
            (keydown)="onKey($event)"
          />
          <kbd>esc</kbd>
        </div>

        <ul id="palette-list" role="listbox" class="palette-list" aria-label="Results">
          @if (pages().length) {
            <li role="presentation" class="palette-group">Pages</li>
            @for (it of pages(); track it.id) {
              <ng-container *ngTemplateOutlet="row; context: { $implicit: it }" />
            }
          }
          @if (companies().length) {
            <li role="presentation" class="palette-group">Companies</li>
            @for (it of companies(); track it.id) {
              <ng-container *ngTemplateOutlet="row; context: { $implicit: it }" />
            }
          }
          @if (actions().length) {
            <li role="presentation" class="palette-group">Actions</li>
            @for (it of actions(); track it.id) {
              <ng-container *ngTemplateOutlet="row; context: { $implicit: it }" />
            }
          }
          @if (!items().length) {
            <li role="presentation" class="palette-empty">
              @if (companiesRes.isLoading()) {
                Searching…
              } @else {
                Nothing matches “{{ q() }}”.
              }
            </li>
          }
        </ul>

        <div class="palette-foot">
          <span><kbd>↑</kbd><kbd>↓</kbd> move</span>
          <span><kbd>↵</kbd> open</span>
          @if (companyCount(); as n) {
            <span class="palette-count">{{ n }} companies indexed</span>
          }
        </div>
      </div>
    </dialog>

    <ng-template #row let-it>
      <li
        role="option"
        [id]="'opt-' + it.id"
        class="palette-item"
        [class.on]="active()?.id === it.id"
        [attr.aria-selected]="active()?.id === it.id"
        (mousemove)="hover(it)"
        (click)="go(it)"
      >
        @if (it.kind === 'company') {
          <span class="palette-ico palette-sym">{{ it.label.slice(0, 4) }}</span>
        } @else {
          <span class="palette-ico"><app-icon [name]="it.icon" /></span>
        }
        <span class="palette-text">
          <span class="palette-label">{{
            it.kind === 'company' ? it.label + ' · ' + it.sub : it.label
          }}</span>
          @if (it.kind !== 'company') {
            <span class="palette-sub">{{ it.sub }}</span>
          }
        </span>
        <app-icon class="palette-go" name="enter" [size]="15" />
      </li>
    </ng-template>
  `,
  styleUrl: './command-palette.css',
})
export class CommandPalette {
  readonly open = model(false);

  private readonly router = inject(Router);
  private readonly auth = inject(AuthService);
  /** Owner-only pages are not offered to a MEMBER in accounts mode (ADR-0005). */
  private readonly pageList = computed(() =>
    pageItems(visibleNav(NAV_GROUPS, this.auth.accountsMode(), this.auth.ownerView())),
  );
  private readonly dlg = viewChild.required<ElementRef<HTMLDialogElement>>('dlg');
  private readonly box = viewChild.required<ElementRef<HTMLInputElement>>('box');

  protected readonly q = signal('');
  private readonly activeIndex = signal(0);

  /** Companies are fetched the first time the palette opens, then kept. */
  private readonly wanted = signal(false);
  protected readonly companiesRes = httpResource<CompanySummary[]>(() =>
    this.wanted() ? apiUrl.companies() : undefined,
  );
  protected readonly companyCount = computed(() => valueOf(this.companiesRes)?.length ?? 0);

  protected readonly pages = computed<Item[]>(() => {
    const terms = this.terms();
    return this.pageList().filter((p) => terms.every((t) => p.haystack.includes(t)));
  });

  protected readonly companies = computed<Item[]>(() => {
    const q = this.q().trim().toLowerCase();
    if (!q) return [];
    const scored: { c: CompanySummary; s: number }[] = [];
    for (const c of valueOf(this.companiesRes) ?? []) {
      const sym = c.symbol.toLowerCase();
      const name = c.name.toLowerCase();
      const s =
        sym === q
          ? 0
          : sym.startsWith(q)
            ? 1
            : name.startsWith(q)
              ? 2
              : name.includes(q)
                ? 3
                : c.sector.toLowerCase().includes(q)
                  ? 4
                  : -1;
      if (s >= 0) scored.push({ c, s });
    }
    return scored
      .sort((a, b) => a.s - b.s || a.c.symbol.localeCompare(b.c.symbol))
      .slice(0, MAX_COMPANIES)
      .map(({ c }) => ({
        id: 'c:' + c.symbol,
        kind: 'company',
        label: c.symbol,
        sub: c.name,
        url: '/companies/' + encodeURIComponent(c.symbol),
      }));
  });

  /** A ticker-looking query that is not a tracked company offers to add it (looked up on SEC EDGAR). */
  protected readonly actions = computed<Item[]>(() => {
    const q = this.q().trim();
    if (!TICKER.test(q) || !this.companiesRes.hasValue()) return [];
    const sym = q.toUpperCase();
    if ((valueOf(this.companiesRes) ?? []).some((c) => c.symbol.toUpperCase() === sym)) return [];
    return [
      {
        id: 'a:add:' + sym,
        kind: 'action',
        label: `Add ${sym} to the universe`,
        sub: 'Looks it up on SEC EDGAR and prefills the form',
        url: '/universe?add=' + encodeURIComponent(sym),
        icon: 'globe',
      },
    ];
  });

  protected readonly items = computed(() => [
    ...this.pages(),
    ...this.companies(),
    ...this.actions(),
  ]);
  protected readonly active = computed(() => {
    const list = this.items();
    return list.length ? list[Math.min(this.activeIndex(), list.length - 1)] : null;
  });

  private readonly terms = computed(() =>
    this.q().trim().toLowerCase().split(/\s+/).filter(Boolean),
  );

  constructor() {
    effect(() => {
      const d = this.dlg().nativeElement;
      if (this.open()) {
        this.wanted.set(true);
        this.q.set('');
        this.activeIndex.set(0);
        if (!d.open) d.showModal();
        this.box().nativeElement.focus();
      } else if (d.open) {
        d.close();
      }
    });
  }

  protected onInput(v: string): void {
    this.q.set(v);
    this.activeIndex.set(0);
  }

  protected onKey(e: KeyboardEvent): void {
    const n = this.items().length;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!n) return;
      const cur = Math.min(this.activeIndex(), n - 1);
      this.activeIndex.set((cur + (e.key === 'ArrowDown' ? 1 : n - 1)) % n);
      const id = this.active()?.id;
      if (id)
        this.dlg()
          .nativeElement.querySelector(`[id="opt-${id}"]`)
          ?.scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const it = this.active();
      if (it) this.go(it);
    }
  }

  protected hover(it: Item): void {
    const i = this.items().findIndex((x) => x.id === it.id);
    if (i >= 0 && i !== this.activeIndex()) this.activeIndex.set(i);
  }

  protected go(it: Item): void {
    this.open.set(false);
    this.router.navigateByUrl(it.url);
  }

  /** A click on the dialog element itself (not its box) is a click on the backdrop. */
  protected backdrop(e: MouseEvent): void {
    if (e.target === this.dlg().nativeElement) this.open.set(false);
  }
}
