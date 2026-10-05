import { Component, computed, input } from '@angular/core';
import { SortDir, Sorter } from '../core/sort';

/**
 * A sortable table header: `<th sortKey="cagr" [sort]="sort">CAGR</th>`.
 * Click toggles the direction; the active column shows a filled arrow.
 */
@Component({
  selector: 'th[sortKey]',
  host: {
    class: 'sortable',
    '[class.sorted]': 'active()',
    '[attr.aria-sort]': 'active() ? (sort().state().dir === "asc" ? "ascending" : "descending") : "none"',
  },
  template: `<button type="button" class="sort-btn" (click)="sort().toggle(sortKey(), defaultDir())" [title]="'Sort by this column'">
    <ng-content />
    <span class="sort-ind" aria-hidden="true">{{ glyph() }}</span>
  </button>`,
})
export class SortTh {
  readonly sortKey = input.required<string>();
  readonly sort = input.required<Sorter>();
  readonly defaultDir = input<SortDir>('desc');
  protected readonly active = computed(() => this.sort().state().key === this.sortKey());
  protected readonly glyph = computed(() => (this.active() ? (this.sort().state().dir === 'asc' ? '▲' : '▼') : '↕'));
}
