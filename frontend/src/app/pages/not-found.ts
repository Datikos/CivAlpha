import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';

@Component({
  selector: 'app-not-found',
  imports: [RouterLink],
  template: `
    <div class="page-head"><h1>Page not found</h1></div>
    <div class="empty-box">
      This page does not exist. Go to <a routerLink="/">current forecasts</a>.
    </div>
  `,
})
export class NotFoundPage {}
