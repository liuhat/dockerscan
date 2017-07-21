#!/usr/bin/env python
# coding=utf-8
import click

@click.command()
@click.argument('x')
@click.argument('y')
@click.argument('z')
def show(x,y,z):
    click.echo('x:%s,y:%s,z:%s' %(x,y,z))
if __name__=='__main__':
    show()
