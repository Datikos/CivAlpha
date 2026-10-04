import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/** 24×24 stroke icons (one or more path strings each). */
const ICONS = {
  pulse: ['M3 12h4l3-8 4 16 3-8h4'],
  history: ['M3 12a9 9 0 1 0 2.6-6.4L3 8', 'M3 3v5h5', 'M12 7.5V12l3 2'],
  target: [
    'M12 3a9 9 0 1 0 0 18 9 9 0 1 0 0-18',
    'M12 7.5a4.5 4.5 0 1 0 0 9 4.5 4.5 0 1 0 0-9',
    'M12 11.2a.8.8 0 1 0 0 1.6.8.8 0 1 0 0-1.6',
  ],
  building: [
    'M5 21V5a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v16',
    'M16 9h2a2 2 0 0 1 2 2v10',
    'M3 21h18',
    'M9 7h3M9 11h3M9 15h3',
  ],
  landmark: ['M3 21h18', 'M3 10h18L12 3.5z', 'M6 10v8M10 10v8M14 10v8M18 10v8'],
  flask: [
    'M9 3h6',
    'M10 3v6.5L4.6 18.4A1.8 1.8 0 0 0 6.1 21h11.8a1.8 1.8 0 0 0 1.5-2.6L14 9.5V3',
    'M7.2 15h9.6',
  ],
  sparkles: [
    'M11 3.5l1.7 4.8 4.8 1.7-4.8 1.7L11 16.5l-1.7-4.8L4.5 10l4.8-1.7z',
    'M18 14.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z',
  ],
  hourglass: ['M6 3h12M6 21h12', 'M7.5 3v3a4.5 4.5 0 0 0 9 0V3', 'M7.5 21v-3a4.5 4.5 0 0 1 9 0v3'],
  globe: [
    'M12 3a9 9 0 1 0 0 18 9 9 0 1 0 0-18',
    'M3 12h18',
    'M12 3c2.5 2.5 3.6 5.6 3.6 9s-1.1 6.5-3.6 9c-2.5-2.5-3.6-5.6-3.6-9S9.5 5.5 12 3',
  ],
  database: [
    'M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3',
    'M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6',
    'M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
  ],
  search: ['M11 4a7 7 0 1 0 0 14 7 7 0 1 0 0-14', 'M20 20l-4-4'],
  menu: ['M4 6h16M4 12h16M4 18h16'],
  close: ['M6 6l12 12M18 6 6 18'],
  sun: [
    'M12 8a4 4 0 1 0 0 8 4 4 0 1 0 0-8',
    'M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4',
  ],
  moon: ['M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z'],
  monitor: [
    'M4 4h16a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z',
    'M8 20h8M12 16v4',
  ],
  arrow: ['M5 12h14M13 6l6 6-6 6'],
  enter: ['M20 5v7a3 3 0 0 1-3 3H5', 'M9 11l-4 4 4 4'],
} as const;

export type IconName = keyof typeof ICONS;

@Component({
  selector: 'app-icon',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'icon', 'aria-hidden': 'true' },
  template: `<svg
    [attr.width]="size()"
    [attr.height]="size()"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.8"
    stroke-linecap="round"
    stroke-linejoin="round"
  >
    @for (d of paths(); track $index) {
      <path [attr.d]="d" />
    }
  </svg>`,
  styles: `
    :host {
      display: inline-flex;
      flex: none;
    }
    svg {
      display: block;
    }
  `,
})
export class Icon {
  readonly name = input.required<IconName>();
  readonly size = input(18);
  protected readonly paths = computed(() => ICONS[this.name()]);
}
